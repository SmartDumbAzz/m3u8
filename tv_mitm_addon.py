from mitmproxy import http

def response(flow: http.HTTPFlow):
    url = flow.request.url
    # Capture the m3u8 link if it contains the unique identifier and ends with .m3u8
    if "cGxheWxpc3QubTN1OA" in url and url.endswith(".m3u8"):
        with open("captured_links.txt", "a") as f:
            f.write("m3u8:" + url + "\n")
