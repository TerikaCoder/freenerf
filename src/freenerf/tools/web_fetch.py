"""Public web page fetching.

Note: URL validation happens before the request and again on every redirect.
DNS is resolved twice (validation and connection), so a deliberately
rebinding hostname could still reach a private address; this is a known
limitation of application-level SSRF filtering without a custom transport.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx
from lxml import html


class WebFetch:
    TIMEOUT = 15
    # Raised from 1 MB: pages up to 4 MB are accepted before extraction.
    MAX_BYTES = 4_000_000
    MAX_CHARS = 12_000
    MAX_REDIRECTS = 5

    USER_AGENT = "Freenerf/2.0 (+terminal coding agent)"
    TEXT_TYPES = (
        "text/html",
        "application/xhtml+xml",
        "text/plain",
        "application/json",
        "application/xml",
        "text/xml",
        "text/markdown",
    )

    def validate_url(self, url):
        try:
            parsed = urlparse(str(url).strip())

            if parsed.scheme not in ("http", "https"):
                return None

            if not parsed.hostname:
                return None

            if parsed.username or parsed.password:
                return None

            host = parsed.hostname.rstrip(".").lower()

            if host == "localhost" or host.endswith(
                (".localhost", ".local", ".internal")
            ):
                return None

            try:
                addresses = [ipaddress.ip_address(host)]
            except ValueError:
                try:
                    addresses = [
                        ipaddress.ip_address(item[4][0])
                        for item in socket.getaddrinfo(
                            host, None, type=socket.SOCK_STREAM
                        )
                    ]
                except OSError:
                    return None

            if not addresses:
                return None

            if any(not address.is_global for address in addresses):
                return None

            return parsed.geturl()

        except (ValueError, TypeError):
            return None

    def fetch(self, url):
        current_url = self.validate_url(url)

        if not current_url:
            return "Web fetch blocked: invalid or non-public URL"

        try:
            with httpx.Client(
                timeout=self.TIMEOUT,
                follow_redirects=False,
                trust_env=False,
                max_redirects=0,
                headers={
                    "User-Agent": self.USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.5",
                },
            ) as client:
                raw_html = bytearray()
                page_encoding = "utf-8"
                content_type = ""
                redirects = 0

                while True:
                    if redirects > self.MAX_REDIRECTS:
                        return "Web fetch error: too many redirects"

                    redirect_url = None

                    with client.stream("GET", current_url) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location:
                                return "Web fetch error: redirect without location"
                            redirect_url = urljoin(current_url, location)
                        else:
                            response.raise_for_status()

                            content_type = response.headers.get(
                                "content-type", ""
                            ).lower()

                            if not any(
                                kind in content_type
                                for kind in self.TEXT_TYPES
                            ):
                                return (
                                    "Web fetch error: URL is not a text or "
                                    "HTML page"
                                )

                            page_encoding = (
                                getattr(response, "encoding", None) or "utf-8"
                            )

                            if not any(
                                kind in content_type
                                for kind in (
                                    "text/html",
                                    "application/xhtml+xml",
                                )
                            ):
                                # Non-HTML text bodies (plain text, JSON, XML)
                                # are returned without HTML extraction.
                                try:
                                    response.read()
                                except AttributeError:
                                    return (
                                        "Web fetch error: URL is not an "
                                        "HTML page"
                                    )

                                body = getattr(response, "content", None)
                                if body is None:
                                    return (
                                        "Web fetch error: URL is not an "
                                        "HTML page"
                                    )

                                if len(body) > self.MAX_BYTES:
                                    return (
                                        "Web fetch error: page exceeds "
                                        "the 4 MB limit"
                                    )
                                raw_html.extend(body)
                                break

                            for chunk in response.iter_bytes():
                                if len(raw_html) + len(chunk) > self.MAX_BYTES:
                                    return (
                                        "Web fetch error: page exceeds "
                                        "the 4 MB limit"
                                    )
                                raw_html.extend(chunk)

                    if redirect_url is None:
                        break

                    redirects += 1
                    current_url = self.validate_url(redirect_url)

                    if not current_url:
                        return "Web fetch blocked: unsafe redirect URL"

            if not raw_html:
                return "Web fetch error: empty page"

            if not any(
                kind in content_type
                for kind in ("text/html", "application/xhtml+xml")
            ):
                text = bytes(raw_html).decode(page_encoding, errors="replace")
                text = " ".join(text.split())
                return (
                    f"URL: {current_url}\n"
                    f"Content-Type: {content_type}\n"
                    f"Content:\n{text[:self.MAX_CHARS]}"
                )

            document = html.fromstring(
                bytes(raw_html),
                parser=html.HTMLParser(
                    encoding=page_encoding,
                    recover=True,
                    no_network=True,
                ),
            )

            for node in document.xpath(
                "//script|//style|//noscript|//svg|//iframe|"
                "//nav|//footer|//header|//aside|//form|"
                "//button|//input"
            ):
                node.drop_tree()

            title_nodes = document.xpath("//title")
            title = (
                " ".join(title_nodes[0].text_content().split())
                if title_nodes else "Untitled"
            )

            main_nodes = document.xpath(
                "//main|//article|//*[@role='main']"
            )
            content_node = main_nodes[0] if main_nodes else document.body

            if content_node is None:
                content_node = document

            content = " ".join(content_node.text_content().split())
            content = content[:self.MAX_CHARS]

            if not content:
                return "Web fetch error: no readable page content"

            return (
                f"Title: {title[:300]}\n"
                f"URL: {current_url}\n"
                f"Content:\n{content}"
            )

        except httpx.TimeoutException:
            return "Web fetch timed out"
        except httpx.HTTPStatusError as exc:
            return f"Web fetch HTTP error: {exc.response.status_code}"
        except httpx.RequestError:
            return "Web fetch connection error"
        except (ValueError, TypeError, OSError):
            return "Web fetch could not parse the page"
        except Exception:
            return "Web fetch failed unexpectedly"
