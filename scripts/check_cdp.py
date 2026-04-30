import urllib.request
try:
    r = urllib.request.urlopen('http://localhost:9222/json/version')
    print(r.read().decode())
except Exception as e:
    print('ERROR:', e)
