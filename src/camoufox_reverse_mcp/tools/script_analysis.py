from __future__ import annotations

import os

from ..server import mcp, browser_manager


@mcp.tool()
async def list_scripts() -> list[dict]:
    """List all JavaScript scripts loaded in the current page.

    Collects both external <script src="..."> and inline <script> elements.
    For inline scripts, returns a preview of the first 200 characters.

    Returns:
        List of dicts with src, type, inline_length, is_module, and preview fields.
    """
    try:
        page = await browser_manager.get_active_page()
        scripts = await page.evaluate("""() => {
            const scripts = document.querySelectorAll('script');
            return Array.from(scripts).map((s, i) => ({
                index: i,
                src: s.src || null,
                type: s.type || 'text/javascript',
                is_module: s.type === 'module',
                inline_length: s.src ? 0 : (s.textContent || '').length,
                preview: s.src ? null : (s.textContent || '').substring(0, 200)
            }));
        }""")
        return scripts
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
async def get_script_source(url: str) -> str:
    """Get the full source code of a JavaScript file by URL.

    For external scripts, fetches the source via the browser's fetch API.
    For inline scripts, pass the index as "inline:<index>" (e.g. "inline:0")
    to get the textContent of the corresponding <script> element.

    Args:
        url: The script URL, or "inline:<index>" for inline scripts.

    Returns:
        The raw JavaScript source code string.
    """
    try:
        page = await browser_manager.get_active_page()
        if url.startswith("inline:"):
            idx = int(url.split(":")[1])
            source = await page.evaluate(f"""() => {{
                const scripts = document.querySelectorAll('script');
                return scripts[{idx}] ? scripts[{idx}].textContent : null;
            }}""")
            return source or f"Inline script at index {idx} not found"
        else:
            source = await page.evaluate(f"""async () => {{
                try {{
                    const resp = await fetch("{url}");
                    return await resp.text();
                }} catch(e) {{
                    return "Fetch error: " + e.message;
                }}
            }}""")
            return source
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def search_code(keyword: str, max_results: int = 20) -> list[dict]:
    """Search for a keyword across all loaded JavaScript sources in the page.

    Fetches each script's source and searches for the keyword, returning
    matching lines with 2 lines of surrounding context.

    Args:
        keyword: The keyword or pattern to search for.
        max_results: Maximum number of matches to return (default 20).

    Returns:
        List of dicts with script_url, line_number, context, and match fields.
    """
    try:
        page = await browser_manager.get_active_page()
        results = await page.evaluate(f"""async () => {{
            const keyword = {repr(keyword)};
            const scripts = document.querySelectorAll('script');
            const results = [];
            const maxResults = {max_results};

            for (const s of scripts) {{
                let source = '';
                let scriptUrl = '';
                if (s.src) {{
                    scriptUrl = s.src;
                    try {{
                        const resp = await fetch(s.src);
                        source = await resp.text();
                    }} catch(e) {{ continue; }}
                }} else {{
                    scriptUrl = 'inline';
                    source = s.textContent || '';
                }}

                const lines = source.split('\\n');
                for (let i = 0; i < lines.length; i++) {{
                    if (results.length >= maxResults) break;
                    if (lines[i].includes(keyword)) {{
                        const start = Math.max(0, i - 2);
                        const end = Math.min(lines.length, i + 3);
                        results.push({{
                            script_url: scriptUrl,
                            line_number: i + 1,
                            match: lines[i].trim().substring(0, 300),
                            context: lines.slice(start, end).join('\\n').substring(0, 1000)
                        }});
                    }}
                }}
                if (results.length >= maxResults) break;
            }}
            return results;
        }}""")
        return results
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
async def save_script(url: str, save_path: str) -> dict:
    """Download a JavaScript file from the page and save it to a local path.

    Args:
        url: URL of the script to download.
        save_path: Local file path to save the script to.

    Returns:
        dict with status, save path, and file size in bytes.
    """
    try:
        page = await browser_manager.get_active_page()
        source = await page.evaluate(f"""async () => {{
            const resp = await fetch("{url}");
            return await resp.text();
        }}""")
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(source)
        return {"status": "saved", "path": save_path, "size": len(source)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_page_html(selector: str | None = None) -> str:
    """Get the full page HTML or the outerHTML of a specific element.

    Args:
        selector: Optional CSS selector. If provided, returns only that
            element's outerHTML. If omitted, returns the full page HTML.

    Returns:
        HTML string of the page or selected element.
    """
    try:
        page = await browser_manager.get_active_page()
        if selector:
            html = await page.evaluate(f"""() => {{
                const el = document.querySelector("{selector}");
                return el ? el.outerHTML : null;
            }}""")
            return html or f"Element not found: {selector}"
        else:
            return await page.content()
    except Exception as e:
        return f"Error: {e}"
