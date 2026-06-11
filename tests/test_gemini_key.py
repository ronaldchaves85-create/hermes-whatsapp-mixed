#!/usr/bin/env python3
import os
import json
import urllib.request
import urllib.error

def test_gemini_connection():
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        print("❌ Error: GOOGLE_API_KEY environment variable is empty or not set.")
        return False

    print(f"Checking API key: {api_key[:8]}...{api_key[-5:] if len(api_key) > 5 else ''}")
    
    # We use gemini-1.5-flash as the standard stable endpoint
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": "Hello, this is a test. Reply with the word 'OK' if you receive this."}]}]
    }

    try:
        req = urllib.request.Request(
            url, 
            data=json.dumps(payload).encode("utf-8"), 
            headers=headers, 
            method="POST"
        )
        print("Sending request to Google Gemini API...")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            try:
                reply = result["candidates"][0]["content"]["parts"][0]["text"].strip()
                print(f"✅ Success! Connection verified. Response: {reply}")
                return True
            except (KeyError, IndexError):
                print(f"⚠️ Response received, but unexpected JSON format: {result}")
                return False
                
    except urllib.error.HTTPError as e:
        status_code = e.code
        try:
            error_body = e.read().decode("utf-8")
            error_json = json.loads(error_body)
            error_msg = error_json.get("error", {}).get("message", "Unknown error")
            error_status = error_json.get("error", {}).get("status", "Unknown status")
        except Exception:
            error_msg = e.reason
            error_status = "HTTP Error"
            
        print(f"❌ API Request Failed (HTTP {status_code}):")
        print(f"   Status : {error_status}")
        print(f"   Message: {error_msg}")
        
        # Specific diagnostic hints
        if status_code == 400:
            if "expired" in error_msg.lower():
                print("\n💡 HINT: The billing account for the Google Cloud project associated with this key is suspended, or the key itself has been deleted/expired.")
            elif "not valid" in error_msg.lower() or "invalid" in error_msg.lower():
                print("\n💡 HINT: The API key format is invalid. Double check for copy-paste typos.")
        elif status_code == 403:
            print("\n💡 HINT: Permission denied. Ensure 'Generative Language API' is enabled and that there are no IP/API restrictions on this key in Google Cloud Console.")
        return False
        
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

if __name__ == "__main__":
    test_gemini_connection()
