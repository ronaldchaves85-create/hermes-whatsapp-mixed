import test from 'node:test';
import assert from 'node:assert';
process.env.WHATSAPP_OWNER_NUMBER = '99999';
process.env.WHATSAPP_ALLOWED_USERS = 'client123';
process.env.WHATSAPP_MODE = 'bot';

const {
  onChatsUpdate,
  onMessagesUpsert,
  getBotPaused,
  setBotPaused,
  getSilencedChats,
  clearSilencedChats,
  getRecentlySentIds,
  getMessageQueue,
  setSock,
  isSystemError
} = await import('../bridge.js');

// Setup Mock Socket
const mockSock = {
  user: {
    id: '12345:1@s.whatsapp.net',
    lid: '67890:1@lid'
  },
  sendMessage: async (chatId, payload) => {
    mockSock.sentMessages.push({ chatId, payload });
    return {
      key: {
        id: 'mock-msg-' + Math.random().toString(36).substring(7),
        fromMe: true,
        remoteJid: chatId
      }
    };
  },
  sentMessages: [],
  ev: {
    on: () => {}
  }
};

// Bind mock socket
setSock(mockSock);

test('WhatsApp Bridge Regression Tests', async (t) => {
  
  t.beforeEach(() => {
    setBotPaused(false);
    clearSilencedChats();
    mockSock.sentMessages = [];
    getRecentlySentIds().clear();
    getMessageQueue().length = 0;
  });

  await t.test('1. Commands in Self-Chat should pause and resume the bot globally', async () => {
    // Owner JID and Self-Chat JID are identical in self-chat mode
    const selfJid = '12345@s.whatsapp.net';
    
    // Simulate stop_bot message
    await onMessagesUpsert({
      messages: [{
        key: {
          id: 'msg-1',
          fromMe: true,
          remoteJid: selfJid,
          participant: selfJid
        },
        message: {
          conversation: 'stop_bot'
        }
      }],
      type: 'notify'
    });

    assert.strictEqual(getBotPaused(), true, 'Bot should be paused after stop_bot in self-chat');
    assert.ok(mockSock.sentMessages.length > 0, 'Should send pause confirmation message');
    assert.ok(mockSock.sentMessages[0].payload.text.includes('pausado'), 'Confirmation should contain paused text');

    // Clear confirmation message
    mockSock.sentMessages = [];

    // Simulate start_bot message
    await onMessagesUpsert({
      messages: [{
        key: {
          id: 'msg-2',
          fromMe: true,
          remoteJid: selfJid,
          participant: selfJid
        },
        message: {
          conversation: 'start_bot'
        }
      }],
      type: 'notify'
    });

    assert.strictEqual(getBotPaused(), false, 'Bot should be resumed after start_bot in self-chat');
    assert.ok(mockSock.sentMessages.length > 0, 'Should send resume confirmation message');
    assert.ok(mockSock.sentMessages[0].payload.text.includes('ativo'), 'Confirmation should contain active text');
  });

  await t.test('2. Commands in Client Chat should NOT be intercepted (bridge remains unchanged)', async () => {
    const clientJid = 'client@s.whatsapp.net';
    const ownerJid = '12345@s.whatsapp.net';
    
    // Owner types stop_bot in client's chat JID (not self-chat)
    await onMessagesUpsert({
      messages: [{
        key: {
          id: 'msg-3',
          fromMe: true,
          remoteJid: clientJid,
          participant: ownerJid
        },
        message: {
          conversation: 'stop_bot'
        }
      }],
      type: 'notify'
    });

    // It should not be intercepted, so:
    // - botPaused should remain false
    // - no confirmation message sent by bridge
    assert.strictEqual(getBotPaused(), false, 'Bot should NOT be paused if stop_bot is typed in client chat');
    assert.strictEqual(mockSock.sentMessages.length, 0, 'No bridge confirmation message should be sent');
  });

  await t.test('3. Regular owner message in client chat should trigger temporary silence', async () => {
    const clientJid = 'client@s.whatsapp.net';
    const ownerJid = '12345@s.whatsapp.net';

    await onMessagesUpsert({
      messages: [{
        key: {
          id: 'msg-4',
          fromMe: true,
          remoteJid: clientJid,
          participant: ownerJid
        },
        message: {
          conversation: 'Hello client, how can I help you?'
        }
      }],
      type: 'notify'
    });

    const silenced = getSilencedChats();
    const duration = silenced[clientJid] - Date.now();
    assert.ok(duration > 0, 'Client chat should be silenced after manual message');
    assert.ok(duration > 590000 && duration <= 600000, `Silence duration should be ~10 minutes, got ${duration} ms`);
  });

  await t.test('4. Command starting with ! in client chat should NOT trigger temporary silence', async () => {
    const clientJid = 'client@s.whatsapp.net';
    const ownerJid = '12345@s.whatsapp.net';

    await onMessagesUpsert({
      messages: [{
        key: {
          id: 'msg-5',
          fromMe: true,
          remoteJid: clientJid,
          participant: ownerJid
        },
        message: {
          conversation: '!suporte status'
        }
      }],
      type: 'notify'
    });

    const silenced = getSilencedChats();
    assert.strictEqual(silenced[clientJid], undefined, 'Client chat should NOT be silenced for commands starting with !');
  });

  await t.test('5. chats.update with unreadCount=0 should trigger temporary silence', async () => {
    const clientJid = 'client@s.whatsapp.net';

    await onChatsUpdate([{
      id: clientJid,
      unreadCount: 0
    }]);

    const silenced = getSilencedChats();
    const duration = silenced[clientJid] - Date.now();
    assert.ok(duration > 0, 'Client chat should be silenced when owner reads it');
    assert.ok(duration > 590000 && duration <= 600000, `Silence duration should be ~10 minutes, got ${duration} ms`);
  });

  await t.test('6. chats.update with unreadCount=0 in self-chat should NOT trigger silence', async () => {
    const selfJid = '12345@s.whatsapp.net';

    await onChatsUpdate([{
      id: selfJid,
      unreadCount: 0
    }]);

    const silenced = getSilencedChats();
    assert.strictEqual(silenced[selfJid], undefined, 'Self-chat should never be silenced');
  });

  await t.test('7. Commands in Bot Mode from owner private chat should pause and resume the bot globally', async () => {
    const ownerJid = '99999@s.whatsapp.net';
    
    // Simulate stop_bot message from owner in their private chat with the bot
    await onMessagesUpsert({
      messages: [{
        key: {
          id: 'msg-6',
          fromMe: false,
          remoteJid: ownerJid
        },
        message: {
          conversation: 'stop_bot'
        }
      }],
      type: 'notify'
    });

    assert.strictEqual(getBotPaused(), true, 'Bot should be paused after stop_bot from owner in direct chat');
    assert.ok(mockSock.sentMessages.length > 0, 'Should send pause confirmation message');
    assert.ok(mockSock.sentMessages[0].payload.text.includes('pausado'), 'Confirmation should contain paused text');

    // Clear confirmation message
    mockSock.sentMessages = [];

    // Simulate start_bot message from owner in direct chat
    await onMessagesUpsert({
      messages: [{
        key: {
          id: 'msg-7',
          fromMe: false,
          remoteJid: ownerJid
        },
        message: {
          conversation: 'start_bot'
        }
      }],
      type: 'notify'
    });

    assert.strictEqual(getBotPaused(), false, 'Bot should be resumed after start_bot from owner in direct chat');
    assert.ok(mockSock.sentMessages.length > 0, 'Should send resume confirmation message');
    assert.ok(mockSock.sentMessages[0].payload.text.includes('ativo'), 'Confirmation should contain active text');
  });

  await t.test('8. Owner regular message should bypass the allowlist check and be enqueued', async () => {
    const ownerJid = '99999@s.whatsapp.net';
    
    await onMessagesUpsert({
      messages: [{
        key: {
          id: 'msg-8',
          fromMe: false,
          remoteJid: ownerJid
        },
        message: {
          conversation: 'Hello bot, please list files'
        }
      }],
      type: 'notify'
    });

    const queue = getMessageQueue();
    assert.strictEqual(queue.length, 1, 'Owner message should bypass allowlist and be enqueued');
    assert.strictEqual(queue[0].body, 'Hello bot, please list files', 'Enqueued message body should match');
  });

  await t.test('9. Client not in allowlist should be ignored and not enqueued', async () => {
    const randomClientJid = 'randomclient@s.whatsapp.net';
    
    await onMessagesUpsert({
      messages: [{
        key: {
          id: 'msg-9',
          fromMe: false,
          remoteJid: randomClientJid
        },
        message: {
          conversation: 'Hello, I want support'
        }
      }],
      type: 'notify'
    });

    const queue = getMessageQueue();
    assert.strictEqual(queue.length, 0, 'Unauthorized client message should be ignored and not enqueued');
  });

  await t.test('10. Owner manual message in group chat should NOT trigger silence', async () => {
    const groupJid = 'group123@g.us';
    const ownerJid = '99999@s.whatsapp.net';

    await onMessagesUpsert({
      messages: [{
        key: {
          id: 'msg-10',
          fromMe: true,
          remoteJid: groupJid,
          participant: ownerJid
        },
        message: {
          conversation: 'Hello group!'
        }
      }],
      type: 'notify'
    });

    const silenced = getSilencedChats();
    assert.strictEqual(silenced[groupJid], undefined, 'Group chat should never be silenced');
  });

  await t.test('11. Owner command in group chat should NOT be intercepted', async () => {
    const groupJid = 'group123@g.us';
    const ownerJid = '99999@s.whatsapp.net';

    await onMessagesUpsert({
      messages: [{
        key: {
          id: 'msg-11',
          fromMe: false,
          remoteJid: groupJid,
          participant: ownerJid
        },
        message: {
          conversation: 'stop_bot'
        }
      }],
      type: 'notify'
    });

    assert.strictEqual(getBotPaused(), false, 'Bot should NOT be paused when command is sent in a group chat');
    assert.strictEqual(mockSock.sentMessages.length, 0, 'No bridge confirmation should be sent to group chat');
  });

  await t.test('12. Commands with trailing/leading spaces and newlines should be successfully intercepted', async () => {
    const ownerJid = '99999@s.whatsapp.net';
    
    // Simulate stop_bot with spaces and capitalization
    await onMessagesUpsert({
      messages: [{
        key: {
          id: 'msg-12',
          fromMe: false,
          remoteJid: ownerJid
        },
        message: {
          conversation: ' \n STOP_BOT \n '
        }
      }],
      type: 'notify'
    });

    assert.strictEqual(getBotPaused(), true, 'Bot should be paused even with spaces/newlines in command');
    assert.ok(mockSock.sentMessages.length > 0, 'Should send pause confirmation');
    
    // Clear and resume
    mockSock.sentMessages = [];
    await onMessagesUpsert({
      messages: [{
        key: {
          id: 'msg-13',
          fromMe: false,
          remoteJid: ownerJid
        },
        message: {
          conversation: '\r\n !retomar \r\n'
        }
      }],
      type: 'notify'
    });

    assert.strictEqual(getBotPaused(), false, 'Bot should be resumed even with spaces/newlines in command');
    assert.ok(mockSock.sentMessages.length > 0, 'Should send resume confirmation');
  });

  await t.test('13. isSystemError filter should catch technical/system messages and allow normal client messages', () => {
    // Blocked system/error messages
    assert.ok(isSystemError('💾 Self-improvement review: Memory updated'), 'Should block memory updates');
    assert.ok(isSystemError('💾 Memory updated'), 'Should block memory updates');
    assert.ok(isSystemError('❌ Rate limited after 3 retries — HTTP 402: This request requires more credits, or fewer max_tokens. You requested up to 65536 tokens, but can only afford 64850. To increase, visit https://openrouter.ai/settings/credits and add more credits'), 'Should block OpenRouter credit errors');
    assert.ok(isSystemError('⏱️ Rate limited. Waiting 2.3s (attempt 2/3)...'), 'Should block rate limit alerts');
    assert.ok(isSystemError('⚠️ Max retries (3) exhausted — trying fallback...'), 'Should block fallback logs');
    assert.ok(isSystemError('Traceback (most recent call last):\n  File "agent.py", line 42, in call_llm\n    raise ValueError("API Key missing")\nValueError: API Key missing'), 'Should block python stack traces');
    assert.ok(isSystemError('Error: connection timed out while calling anthropic API'), 'Should block connection timeouts');
    assert.ok(isSystemError('{"error": "Unauthorized Access", "status": 401}'), 'Should block JSON errors');

    // Allowed normal client/owner messages
    assert.ok(!isSystemError('Oi André, tudo bem?'), 'Should allow simple greeting');
    assert.ok(!isSystemError('Oi, o cliente está sem créditos no painel de Chatcommerce?'), 'Should allow normal credit discussion in Portuguese');
    assert.ok(!isSystemError('Preciso resolver um problema de integração com a API'), 'Should allow normal developer API discussion in Portuguese');
    assert.ok(!isSystemError('⚠️ Obrigado por avisar!'), 'Should allow regular emoji messages without technical keywords');
  });
});
