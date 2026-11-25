import httpx


class WebSearch:
    API_URL = "https://freeserp.ai/api.php"
    TIMEOUT = 20

    def search(self, query, max_results=5):
        query = str(query).strip()

        if not query:
            return "Search query is empty"

        try:
            max_results = max(1, min(int(max_results), 5))
        except (TypeError, ValueError):
            max_results = 5

        try:
            response = httpx.get(
                self.API_URL,
                params={
                    "index": "web",
                    "q": query,
                    "size": max_results,
                },
                headers={"User-Agent": "Freenerf/1.0"},
                timeout=self.TIMEOUT,
                follow_redirects=True,
            )
            response.raise_for_status()
            data = response.json()

            if not isinstance(data, dict) or data.get("ok") is not True:
                return "Web search provider returned an invalid response"

            results = data.get("results", [])

            if not isinstance(results, list) or not results:
                return "No web results found"

            output = []

            for index, item in enumerate(results[:max_results], 1):
                if not isinstance(item, dict):
                    continue

                title = str(item.get("title") or "Untitled")[:300]
                url = str(item.get("url") or "")[:1000]
                domain = str(item.get("domain") or "")[:200]
                snippet = str(item.get("snippet") or "")[:1200]

                if not url.startswith(("https://", "http://")):
                    continue

                output.append(
                    f"{index}. {title}\n"
                    f"Domain: {domain}\n"
                    f"URL: {url}\n"
                    f"Snippet: {snippet}"
                )

            return "\n\n".join(output) if output else "No valid web results"

        except httpx.TimeoutException:
            return "Web search timed out"
        except httpx.HTTPStatusError as exc:
            return f"Web search HTTP error: {exc.response.status_code}"
        except httpx.RequestError as exc:
            return f"Web search connection error: {exc}"
        except (ValueError, TypeError):
            return "Web search returned invalid JSON"
