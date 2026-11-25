//! Bounded conversation context.
//!
//! Oldest complete user turns are dropped first, tool output is truncated
//! before that, and an oversized current turn is shortened from the middle.
//! Assistant tool calls never become orphaned.

use freenerf_core::Message;

pub const CONTEXT_TOOL_LIMIT: usize = 2_000;

pub fn is_tool_result(message: &Message) -> bool {
    message.role == "user" && message.text().starts_with("[tool result]")
}

fn size_of(message: &Message) -> usize {
    let mut total = message.text().chars().count();
    for call in &message.tool_calls {
        total += call.name.chars().count() + call.arguments.chars().count();
    }
    total
}

fn total_size(messages: &[Message]) -> usize {
    messages.iter().map(size_of).sum()
}

fn turn_starts(messages: &[Message]) -> Vec<usize> {
    messages
        .iter()
        .enumerate()
        .filter(|(_, message)| message.role == "user" && !is_tool_result(message))
        .map(|(index, _)| index)
        .collect()
}

fn take_chars(value: &str, limit: usize) -> String {
    value.chars().take(limit).collect()
}

fn tail_chars(value: &str, limit: usize) -> String {
    let count = value.chars().count();
    if count <= limit {
        return value.to_string();
    }
    value.chars().skip(count - limit).collect()
}

pub fn trim_context(messages: &[Message], max_chars: usize) -> Vec<Message> {
    let mut bounded: Vec<Message> = messages.to_vec();

    for message in &mut bounded {
        if message.role == "tool" || is_tool_result(message) {
            let content = message.text().to_string();
            if content.chars().count() > CONTEXT_TOOL_LIMIT {
                let kept = take_chars(&content, CONTEXT_TOOL_LIMIT);
                message.content = Some(format!(
                    "{kept}\n[tool output truncated to preserve context]"
                ));
            }
        }
    }

    let mut total = total_size(&bounded);
    let mut starts = turn_starts(&bounded);

    while total > max_chars && starts.len() > 1 {
        let start = starts[0];
        let end = starts[1];
        let removed: usize = bounded[start..end].iter().map(size_of).sum();
        total = total.saturating_sub(removed);
        bounded.drain(start..end);
        starts = turn_starts(&bounded);
    }

    if total > max_chars {
        let system_index = if bounded.first().map(|m| m.role.as_str()) == Some("system") {
            Some(0)
        } else {
            None
        };

        for index in 0..bounded.len() {
            if total <= max_chars {
                break;
            }
            if Some(index) == system_index || !bounded[index].tool_calls.is_empty() {
                continue;
            }

            let content = bounded[index].text().to_string();
            if content.is_empty() {
                continue;
            }
            let excess = total - max_chars;
            let length = content.chars().count();
            let target_length = std::cmp::max(256, length.saturating_sub(excess + 64));
            if target_length >= length {
                continue;
            }

            let head_length = std::cmp::max(128, target_length * 7 / 10);
            let tail_length = target_length.saturating_sub(head_length);
            let mut shortened = take_chars(&content, head_length);
            shortened.push_str("\n...[earlier content truncated]...\n");
            if tail_length > 0 {
                shortened.push_str(&tail_chars(&content, tail_length));
            }
            bounded[index].content = Some(shortened);
            total = total_size(&bounded);
        }
    }

    bounded
}

#[cfg(test)]
mod tests {
    use super::*;

    fn user(text: &str) -> Message {
        Message::user(text)
    }

    #[test]
    fn keeps_short_conversations_untouched() {
        let messages = vec![Message::system("sys"), user("hi"), Message::assistant("hello")];
        assert_eq!(trim_context(&messages, 100_000), messages);
    }

    #[test]
    fn truncates_large_tool_output_first() {
        let messages = vec![
            Message::system("sys"),
            user("read it"),
            Message::tool("call_1", "x".repeat(10_000)),
        ];
        let trimmed = trim_context(&messages, 100_000);
        assert!(trimmed[2].text().contains("truncated to preserve context"));
        assert!(trimmed[2].text().chars().count() < 2_100);
    }

    #[test]
    fn drops_oldest_turns_and_keeps_system_and_latest() {
        let messages = vec![
            Message::system("system"),
            user("old turn"),
            Message::assistant("old answer"),
            user("new turn"),
            Message::assistant("new answer"),
        ];
        let trimmed = trim_context(&messages, 40);
        assert_eq!(trimmed[0].role, "system");
        assert!(trimmed.iter().any(|m| m.text() == "new turn"));
        assert!(!trimmed.iter().any(|m| m.text() == "old turn"));
    }

    #[test]
    fn never_orphans_tool_calls() {
        let messages = vec![
            Message::system("sys"),
            user("first"),
            Message {
                role: "assistant".into(),
                content: Some("calling".into()),
                tool_calls: vec![freenerf_core::ToolCall {
                    id: "c1".into(),
                    name: "read_file".into(),
                    arguments: "{}".into(),
                }],
                ..Default::default()
            },
            Message::tool("c1", "result"),
            user("second"),
        ];
        let trimmed = trim_context(&messages, 30);
        // Either the whole first turn is gone, or its tool result is present.
        let has_call = trimmed.iter().any(|m| !m.tool_calls.is_empty());
        let has_result = trimmed.iter().any(|m| m.role == "tool");
        assert_eq!(has_call, has_result);
        assert!(trimmed.iter().any(|m| m.text() == "second"));
    }
}
