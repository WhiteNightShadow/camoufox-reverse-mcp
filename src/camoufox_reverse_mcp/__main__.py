import argparse
from .server import mcp


def main():
    parser = argparse.ArgumentParser(description="Camoufox Reverse Engineering MCP Server")
    parser.add_argument("--proxy", type=str, help="Proxy server URL (e.g. http://127.0.0.1:7890)")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    parser.add_argument("--os", type=str, default="auto",
                        choices=["auto", "windows", "macos", "linux"],
                        help="OS fingerprint to emulate (default: auto-detect host OS)")
    parser.add_argument("--locale", type=str, default="auto",
                        help="Browser locale, e.g. zh-CN, en-US (default: auto-detect)")
    parser.add_argument("--geoip", action="store_true", help="Enable GeoIP inference from proxy")
    parser.add_argument("--humanize", action="store_true", help="Enable humanized mouse movement")
    parser.add_argument("--block-images", action="store_true", help="Block image loading")
    parser.add_argument("--block-webrtc", action="store_true", help="Block WebRTC")
    args = parser.parse_args()

    from .browser import BrowserManager
    BrowserManager.default_config = {
        "proxy": {"server": args.proxy} if args.proxy else None,
        "headless": args.headless,
        "os": args.os,
        "locale": args.locale,
        "geoip": args.geoip,
        "humanize": args.humanize,
        "block_images": args.block_images,
        "block_webrtc": args.block_webrtc,
    }

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
