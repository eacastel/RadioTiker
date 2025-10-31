@router.api_route("/relay/{user_id}/{track_id}", methods=["GET", "HEAD"])
def relay(user_id: str, track_id: str, request: Request):
    lib = load_lib(user_id)
    track = lib["tracks"].get(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Unknown track_id")

    url = build_stream_url(user_id, track)
    if not url:
        raise HTTPException(status_code=503, detail="Agent offline or base_url/rel_path unknown")

    # Forward only what the client asked. Do not fabricate ranges.
    # Build headers minimal + pass client Range through if present.
    fwd_headers = {
        "User-Agent": "RadioTiker-Relay/0.4",
        "Accept": request.headers.get("accept") or "*/*",
    }
    if "range" in request.headers:
        fwd_headers["Range"] = request.headers.get("range")
    if "if-range" in request.headers:
        fwd_headers["If-Range"] = request.headers.get("if-range")

    try:
        if request.method == "HEAD":
            upstream = requests.head(url, timeout=(5, 15), headers=fwd_headers, allow_redirects=True)
        else:
            upstream = requests.get(url, timeout=(5, 300), headers=fwd_headers, allow_redirects=True, stream=True)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Upstream fetch failed: {e}")

    status = upstream.status_code
    if status >= 400:
        raise HTTPException(status_code=status, detail="Upstream error")

    # Only pass through safe, relevant headers. Do not synthesize Content-Range.
    passthrough = {}
    for k in [
        "Content-Type", "Content-Length", "Content-Range", "Accept-Ranges",
        "Cache-Control", "ETag", "Last-Modified", "Expires"
    ]:
        v = upstream.headers.get(k)
        if v:
            passthrough[k] = v

    # Make CORS and caching explicit for browsers:
    passthrough["Access-Control-Allow-Origin"] = "*"
    # iOS Safari can loop if the connection is kept alive on chunked streams:
    passthrough["Connection"] = "close"
    passthrough.setdefault("Cache-Control", "no-store, must-revalidate")

    media = upstream.headers.get("Content-Type") or "audio/mpeg"

    if request.method == "HEAD":
        return Response(status_code=status, headers=passthrough, media_type=media)

    def gen():
        try:
            for chunk in upstream.iter_content(chunk_size=128 * 1024):
                if chunk:
                    yield chunk
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    return StreamingResponse(gen(), media_type=media, headers=passthrough, status_code=status)
