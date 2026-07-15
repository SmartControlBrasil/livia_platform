def get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        first_ip = forwarded_for.split(",", 1)[0].strip()
        if first_ip:
            return first_ip

    remote_addr = request.META.get("REMOTE_ADDR", "")
    if remote_addr:
        return remote_addr
    return "unknown"
