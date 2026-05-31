import json
import urllib.request
import urllib.parse

SCRIPTS_DIR = r"C:\Users\lyh17\.agents\skills\redbookskills\scripts"

def get_cdp_command(endpoint, expression):
    """Execute JS via CDP HTTP API"""
    # First get the debugger URL for the page
    with urllib.request.urlopen("http://localhost:9222/json", timeout=10) as resp:
        tabs = json.loads(resp.read().decode())
    
    if not tabs:
        raise Exception("No browser tabs found. Make sure Edge is running with --remote-debugging-port=9222")
    
    # Find the first page (Xiaohongshu or any page)
    tab = tabs[0]
    debugger_url = tab.get("webSocketDebuggerUrl")
    if not debugger_url:
        raise Exception(f"No websocket URL for tab: {tab.get('title', 'unknown')}")
    
    print(f"Using tab: {tab.get('title', 'unknown')} - {tab.get('url', 'unknown')[:50]}")
    
    # We can't directly call CDP WebSocket from urllib, so use HTTP JSONP approach
    # Actually CDP over HTTP uses a different mechanism
    # Let me try the /json/version endpoint we know works
    return tab

def get_ws_url():
    """Get WebSocket URL from CDP HTTP endpoint"""
    with urllib.request.urlopen("http://localhost:9222/json/version", timeout=10) as resp:
        data = json.loads(resp.read().decode())
    return data["webSocketDebuggerUrl"]

if __name__ == "__main__":
    try:
        ws_url = get_ws_url()
        print(f"WebSocket URL: {ws_url}")
        
        # Save it for reference
        with open(f"{SCRIPTS_DIR}\\edge_ws_url.txt", "w") as f:
            f.write(ws_url)
        print(f"Saved to edge_ws_url.txt")
        
    except Exception as e:
        print(f"Error: {e}")
