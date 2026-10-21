import llm_researcher
import json

def verify():
    print("--- DeepSeek Connectivity Test ---")
    if not llm_researcher.is_enabled():
        print("[!] Error: LLM is not enabled. Check LLM_PROVIDER and LLM_API_KEY in .env")
        return

    print(f"[+] Provider: {llm_researcher.PROVIDER}")
    print("[+] Sending test prompt...")
    
    test_prompt = "Return a JSON object with a 'status' field set to 'ok'."
    response = llm_researcher._call_llm(test_prompt)
    
    if response:
        print(f"[+] Raw Response: {response.strip()}")
        print("[SUCCESS] DeepSeek is connected and responding.")
    else:
        print("[FAILURE] No response from DeepSeek. Check API key balance or internet connection.")

if __name__ == "__main__":
    verify()