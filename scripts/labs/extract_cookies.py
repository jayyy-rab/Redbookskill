import sys
sys.path.insert(0, 'C:/Users/lyh17/.agents/skills/redbookskills/scripts')
from cookie_getter import *

cdp_url = get_cdp_url()
ws_url = get_ws_url()
print(f"CDP: {cdp_url}")
print(f"WS: {ws_url}")

# Extract cookies
cookies = get_cookies(ws_url)
if cookies:
    save_cookies(cookies, 'cookies_for_api.json')
    print(f"Saved {len(cookies)} cookies")
else:
    print("No cookies found - may need to login first")
