"""Read the Network codec without importing either video runtime."""


def requested_codec(params):
  value = params.get("ClusterNetworkCodec") or "jpeg"
  if isinstance(value, bytes):
    value = value.decode("utf-8", errors="replace")
  value = str(value).strip().lower()
  return value if value in ("jpeg", "h264") else "jpeg"
