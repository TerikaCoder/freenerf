//! Web search and safe public page fetching.
//!
//! The fetcher reproduces the Python SSRF checks: only http(s), no embedded
//! credentials, no localhost/.local/.internal hosts, every resolved address
//! must be globally routable, and each redirect is re-validated.

use std::io::Read;
use std::net::{IpAddr, ToSocketAddrs};
use std::time::Duration;

use regex::Regex;
use url::Url;

pub struct WebSearch {
    client: reqwest::blocking::Client,
}

impl WebSearch {
    pub const API_URL: &'static str = "https://freeserp.ai/api.php";

    pub fn new() -> Self {
        Self {
            client: reqwest::blocking::Client::builder()
                .timeout(Duration::from_secs(20))
                .build()
                .unwrap_or_default(),
        }
    }

    pub fn search(&self, query: &str, max_results: i64) -> String {
        let query = query.trim();
        if query.is_empty() {
            return "Search query is empty".into();
        }
        let max_results = max_results.clamp(1, 5);

        let response = self
            .client
            .get(Self::API_URL)
            .query(&[
                ("index", "web"),
                ("q", query),
                ("size", &max_results.to_string()),
            ])
            .header("User-Agent", "Freenerf/1.0")
            .send();

        let response = match response {
            Ok(response) => response,
            Err(error) => {
                if error.is_timeout() {
                    return "Web search timed out".into();
                }
                return format!("Web search connection error: {error}");
            }
        };

        if !response.status().is_success() {
            return format!("Web search HTTP error: {}", response.status().as_u16());
        }

        let data: serde_json::Value = match response.json() {
            Ok(value) => value,
            Err(_) => return "Web search returned invalid JSON".into(),
        };

        if data.get("ok").and_then(|v| v.as_bool()) != Some(true) {
            return "Web search provider returned an invalid response".into();
        }

        let results = data
            .get("results")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        if results.is_empty() {
            return "No web results found".into();
        }

        let mut output = Vec::new();
        for (index, item) in results.iter().take(max_results as usize).enumerate() {
            let title = item
                .get("title")
                .and_then(|v| v.as_str())
                .unwrap_or("Untitled");
            let url = item.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let domain = item.get("domain").and_then(|v| v.as_str()).unwrap_or("");
            let snippet = item.get("snippet").and_then(|v| v.as_str()).unwrap_or("");

            if !(url.starts_with("https://") || url.starts_with("http://")) {
                continue;
            }

            output.push(format!(
                "{}. {}\nDomain: {}\nURL: {}\nSnippet: {}",
                index + 1,
                truncate(title, 300),
                truncate(domain, 200),
                truncate(url, 1000),
                truncate(snippet, 1200)
            ));
        }

        if output.is_empty() {
            "No valid web results".into()
        } else {
            output.join("\n\n")
        }
    }
}

impl Default for WebSearch {
    fn default() -> Self {
        Self::new()
    }
}

pub struct WebFetch {
    client: reqwest::blocking::Client,
}

/// Pages larger than this are rejected. Raised from 1 MB to 4 MB so the fetch
/// engine comfortably clears the old ceiling.
pub const MAX_BYTES: usize = 4_000_000;
const MAX_CHARS: usize = 12_000;
const MAX_REDIRECTS: usize = 5;

impl WebFetch {
    pub fn new() -> Self {
        Self {
            client: reqwest::blocking::Client::builder()
                .timeout(Duration::from_secs(15))
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .unwrap_or_default(),
        }
    }

    /// Validate a URL and return it only when it is a public http(s) address.
    pub fn validate_url(raw: &str) -> Option<Url> {
        let parsed = Url::parse(raw.trim()).ok()?;
        if parsed.scheme() != "http" && parsed.scheme() != "https" {
            return None;
        }
        if parsed.username() != "" || parsed.password().is_some() {
            return None;
        }
        let host = parsed.host_str()?.trim_end_matches('.').to_lowercase();
        if host == "localhost"
            || host.ends_with(".localhost")
            || host.ends_with(".local")
            || host.ends_with(".internal")
        {
            return None;
        }

        let addresses: Vec<IpAddr> = if let Ok(ip) = host.parse::<IpAddr>() {
            vec![ip]
        } else {
            (host.as_str(), 0u16)
                .to_socket_addrs()
                .ok()?
                .map(|addr| addr.ip())
                .collect()
        };

        if addresses.is_empty() || addresses.iter().any(|ip| !is_global(*ip)) {
            return None;
        }

        Some(parsed)
    }

    pub fn fetch(&self, raw_url: &str) -> String {
        let mut current = match Self::validate_url(raw_url) {
            Some(url) => url,
            None => return "Web fetch blocked: invalid or non-public URL".into(),
        };

        let mut redirects = 0;

        loop {
            if redirects > MAX_REDIRECTS {
                return "Web fetch error: too many redirects".into();
            }

            let response = match self
                .client
                .get(current.clone())
                .header(
                    "User-Agent",
                    "Freenerf/2.0 (+android coding agent)",
                )
                .header(
                    "Accept",
                    "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.5",
                )
                .send()
            {
                Ok(response) => response,
                Err(error) => {
                    if error.is_timeout() {
                        return "Web fetch timed out".into();
                    }
                    return "Web fetch connection error".into();
                }
            };

            let status = response.status();
            if status.is_redirection() {
                let Some(location) = response
                    .headers()
                    .get("location")
                    .and_then(|v| v.to_str().ok())
                else {
                    return "Web fetch error: redirect without location".into();
                };
                let Ok(next) = current.join(location) else {
                    return "Web fetch blocked: unsafe redirect URL".into();
                };
                match Self::validate_url(next.as_str()) {
                    Some(url) => current = url,
                    None => return "Web fetch blocked: unsafe redirect URL".into(),
                }
                redirects += 1;
                continue;
            }

            if !status.is_success() {
                return format!("Web fetch HTTP error: {}", status.as_u16());
            }

            let content_type = response
                .headers()
                .get("content-type")
                .and_then(|v| v.to_str().ok())
                .unwrap_or("")
                .to_lowercase();

            let is_html = content_type.contains("text/html")
                || content_type.contains("application/xhtml+xml");
            let is_text = is_html
                || content_type.contains("text/plain")
                || content_type.contains("application/json")
                || content_type.contains("application/xml")
                || content_type.contains("text/xml")
                || content_type.contains("text/markdown")
                || content_type.is_empty();

            if !is_text {
                return "Web fetch error: URL is not a text or HTML page".into();
            }

            let mut bytes = Vec::new();
            let mut handle = response.take((MAX_BYTES + 1) as u64);
            if let Err(error) = handle.read_to_end(&mut bytes) {
                return format!("Web fetch error: {error}");
            }
            if bytes.len() > MAX_BYTES {
                return "Web fetch error: page exceeds the 4 MB limit".into();
            }
            if bytes.is_empty() {
                return "Web fetch error: empty page".into();
            }

            let text = String::from_utf8_lossy(&bytes).to_string();

            if !is_html {
                let collapsed = text.split_whitespace().collect::<Vec<_>>().join(" ");
                return format!(
                    "URL: {current}\nContent-Type: {content_type}\nContent:\n{}",
                    truncate(&collapsed, MAX_CHARS)
                );
            }

            let title = extract_title(&text).unwrap_or_else(|| "Untitled".into());
            let body = extract_readable_text(&text);
            if body.trim().is_empty() {
                return "Web fetch error: no readable page content".into();
            }

            return format!(
                "Title: {}\nURL: {current}\nContent:\n{}",
                truncate(&title, 300),
                truncate(&body, MAX_CHARS)
            );
        }
    }
}

impl Default for WebFetch {
    fn default() -> Self {
        Self::new()
    }
}

fn is_global(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => {
            !(v4.is_private()
                || v4.is_loopback()
                || v4.is_link_local()
                || v4.is_broadcast()
                || v4.is_documentation()
                || v4.is_unspecified()
                || v4.is_multicast()
                || v4.octets()[0] == 0
                || (v4.octets()[0] == 100 && (64..128).contains(&v4.octets()[1])))
        }
        IpAddr::V6(v6) => {
            !(v6.is_loopback()
                || v6.is_unspecified()
                || v6.is_multicast()
                || v6.is_unique_local()
                || v6.is_unicast_link_local())
        }
    }
}

fn strip_blocks(html: &str) -> String {
    // The regex crate has no backreferences, so each element is stripped with
    // its own anchored pattern.
    const TAGS: &[&str] = &[
        "script", "style", "noscript", "svg", "iframe", "nav", "footer", "header", "aside",
        "form", "button", "input",
    ];
    let mut text = html.to_string();
    for tag in TAGS {
        let paired = Regex::new(&format!("(?is)<{tag}\\b[^>]*>.*?</{tag}\\s*>"))
            .expect("static regex");
        text = paired.replace_all(&text, " ").to_string();
        let void = Regex::new(&format!("(?is)<{tag}\\b[^>]*/?>"))
            .expect("static regex");
        text = void.replace_all(&text, " ").to_string();
    }
    text
}

fn extract_title(html: &str) -> Option<String> {
    let pattern = Regex::new(r"(?is)<title[^>]*>(.*?)</title>").ok()?;
    let captures = pattern.captures(html)?;
    let title = decode_entities(captures.get(1)?.as_str());
    let collapsed = title.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.is_empty() {
        None
    } else {
        Some(collapsed)
    }
}

fn extract_readable_text(html: &str) -> String {
    let without_blocks = strip_blocks(html);

    // Prefer the main/article body when present.
    let main_pattern = Regex::new(r"(?is)<main\b[^>]*>(.*?)</main>").expect("static regex");
    let article_pattern =
        Regex::new(r"(?is)<article\b[^>]*>(.*?)</article>").expect("static regex");
    let body_pattern = Regex::new(r"(?is)<body\b[^>]*>(.*?)</body>").expect("static regex");

    let content = main_pattern
        .captures(&without_blocks)
        .and_then(|caps| caps.get(1).map(|m| m.as_str().to_string()))
        .or_else(|| {
            article_pattern
                .captures(&without_blocks)
                .and_then(|caps| caps.get(1).map(|m| m.as_str().to_string()))
        })
        .or_else(|| {
            body_pattern
                .captures(&without_blocks)
                .and_then(|caps| caps.get(1).map(|m| m.as_str().to_string()))
        })
        .unwrap_or(without_blocks);

    let tags = Regex::new(r"(?is)<[^>]+>").expect("static regex");
    let spaced = tags.replace_all(&content, " ");
    let decoded = decode_entities(&spaced);
    decoded.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn decode_entities(text: &str) -> String {
    let mut value = text
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&apos;", "'");

    let numeric = Regex::new(r"&#(x?[0-9a-fA-F]+);").expect("static regex");
    value = numeric
        .replace_all(&value, |caps: &regex::Captures<'_>| {
            let raw = &caps[1];
            let parsed = if let Some(hex) = raw.strip_prefix('x').or_else(|| raw.strip_prefix('X')) {
                u32::from_str_radix(hex, 16).ok()
            } else {
                raw.parse::<u32>().ok()
            };
            parsed
                .and_then(char::from_u32)
                .map(|c| c.to_string())
                .unwrap_or_default()
        })
        .to_string();

    value
}

fn truncate(value: &str, limit: usize) -> String {
    if value.chars().count() <= limit {
        return value.to_string();
    }
    value.chars().take(limit).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_private_and_local_urls() {
        for url in [
            "file:///etc/passwd",
            "http://localhost:8080/x",
            "http://127.0.0.1/",
            "http://[::1]/",
            "http://169.254.1.1/",
            "http://user:pass@example.com/",
            "http://something.internal/",
            "not a url",
        ] {
            assert!(WebFetch::validate_url(url).is_none(), "{url} should be rejected");
        }
    }

    #[test]
    fn accepts_public_urls() {
        assert!(WebFetch::validate_url("https://example.com/docs").is_some());
    }

    #[test]
    fn extracts_title_and_readable_text() {
        let html = r#"<html><head><title>Hello &amp; welcome</title>
            <style>body{color:red}</style></head>
            <body><nav>menu</nav><main><h1>Title</h1>
            <p>Some <b>readable</b> text&nbsp;here.</p>
            <script>alert(1)</script></main><footer>foot</footer></body></html>"#;
        assert_eq!(extract_title(html).unwrap(), "Hello & welcome");
        let text = extract_readable_text(html);
        assert!(text.contains("Some readable text here."), "{text}");
        assert!(!text.contains("alert"), "{text}");
        assert!(!text.contains("menu"), "{text}");
        assert!(!text.contains("foot"), "{text}");
    }

    #[test]
    fn decodes_numeric_entities() {
        assert_eq!(decode_entities("caf&#233; &#x2764;"), "café ❤");
    }
}
