
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
ET.register_namespace("", NAMESPACE)


def build_sitemap(domain: str) -> int:
    domain = domain.strip().lower()

    if not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
        domain,
    ):
        raise ValueError("Provide a real hostname, such as example.com.")

    pages = sorted(PUBLIC.rglob("*.html"))
    urls = []

    for page in pages:
        relative = page.relative_to(PUBLIC)

        # Only index.html is treated as the directory landing page.
        if relative.name == "index.html":
            route = relative.parent.as_posix()
            path = "/" if route == "." else f"/{route}/"
        else:
            path = "/" + relative.as_posix()

        modified = datetime.fromtimestamp(
            page.stat().st_mtime, timezone.utc
        ).date().isoformat()

        urls.append((f"https://{domain}{path}", modified))

    if not urls:
        raise RuntimeError("No HTML pages were found in public/.")

    urlset = ET.Element(f"{{{NAMESPACE}}}urlset")

    for url, modified in urls:
        entry = ET.SubElement(urlset, f"{{{NAMESPACE}}}url")
        ET.SubElement(entry, f"{{{NAMESPACE}}}loc").text = url
        ET.SubElement(entry, f"{{{NAMESPACE}}}lastmod").text = modified

    sitemap_path = PUBLIC / "sitemap.xml"
    tree = ET.ElementTree(urlset)
    ET.indent(tree, space="  ")
    tree.write(sitemap_path, encoding="utf-8", xml_declaration=True)

    (PUBLIC / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: https://{domain}/sitemap.xml\n",
        encoding="utf-8",
    )

    print(f"Generated {len(urls)} URLs: {sitemap_path}")
    return len(urls)


if __name__ == "__main__":
    site_domain = os.getenv("SITE_DOMAIN", "")
    if not site_domain:
        raise SystemExit("Set SITE_DOMAIN to your actual deployed hostname.")
    build_sitemap(site_domain)
