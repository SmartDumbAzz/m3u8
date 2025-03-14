from mitmproxy import http
from urllib.parse import urlparse, urlunparse

def response(flow: http.HTTPFlow):
    url = flow.request.url
    if "jwplayer6" not in url and url.endswith("master.m3u8"):
        modified = urlunparse(urlparse(url)._replace(
            path=urlparse(url).path.replace("master.m3u8", "index-f1-v1-a1.m3u8")
        ))
        with open("captured_links.txt", "a") as f:
            f.write("m3u8:{}\n".format(modified))
    elif url.endswith(".vtt"):
        with open("captured_links.txt", "a") as f:
            f.write("vtt:{}\n".format(url))
    