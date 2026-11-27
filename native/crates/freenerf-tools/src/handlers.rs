//! Built-in tool handlers. Each one receives validated arguments.

use std::time::Duration;

use freenerf_core::{Error, Result};
use serde_json::Value;

use crate::image::SUPPORTED_SIZES;
use crate::ToolContext;

fn string_arg(args: &Value, key: &str) -> String {
    args.get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

fn optional_usize(args: &Value, key: &str) -> Option<usize> {
    args.get(key)
        .and_then(|v| v.as_u64())
        .map(|value| value as usize)
}

fn json_text(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| value.to_string())
}

// ----------------------------------------------------------------------
// Filesystem
// ----------------------------------------------------------------------
pub fn list_files(ctx: &ToolContext, args: &Value) -> Result<String> {
    let path = string_arg(args, "path");
    let path = if path.trim().is_empty() {
        "."
    } else {
        path.as_str()
    };
    let recursive = args
        .get("recursive")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    ctx.workspace.list_files(path, recursive)
}

pub fn read_file(ctx: &ToolContext, args: &Value) -> Result<String> {
    let offset = optional_usize(args, "offset").unwrap_or(0);
    let limit = optional_usize(args, "limit");
    ctx.workspace
        .read_file(&string_arg(args, "path"), offset, limit)
}

pub fn file_info(ctx: &ToolContext, args: &Value) -> Result<String> {
    ctx.workspace.file_info(&string_arg(args, "path"))
}

pub fn search(ctx: &ToolContext, args: &Value) -> Result<String> {
    let scope = {
        let scope = string_arg(args, "scope");
        if scope.trim().is_empty() {
            "source".to_string()
        } else {
            scope
        }
    };
    ctx.workspace.search(&string_arg(args, "query"), &scope)
}

pub fn project_info(ctx: &ToolContext, args: &Value) -> Result<String> {
    let _ = args;
    Ok(ctx.project.summary())
}

pub fn write_file(ctx: &ToolContext, args: &Value) -> Result<String> {
    ctx.workspace
        .write_file(&string_arg(args, "path"), &string_arg(args, "content"))
}

pub fn edit_file(ctx: &ToolContext, args: &Value) -> Result<String> {
    let replace_all = args
        .get("replace_all")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    ctx.workspace.edit_file(
        &string_arg(args, "path"),
        &string_arg(args, "old"),
        &string_arg(args, "new"),
        replace_all,
    )
}

pub fn delete_file(ctx: &ToolContext, args: &Value) -> Result<String> {
    ctx.workspace.delete_file(&string_arg(args, "path"))
}

// ----------------------------------------------------------------------
// Shell / git / verify
// ----------------------------------------------------------------------
pub fn run_shell(ctx: &ToolContext, args: &Value) -> Result<String> {
    let command = string_arg(args, "command");
    let timeout = args
        .get("timeout")
        .and_then(|v| v.as_u64())
        .unwrap_or(120);
    let result = ctx
        .shell
        .run(&command, ctx.workspace.root(), Duration::from_secs(timeout));
    let output = result.formatted();
    if !result.succeeded() {
        return Err(Error::Tool(output));
    }
    Ok(output)
}

pub fn verify(ctx: &ToolContext, args: &Value) -> Result<String> {
    let mut command = string_arg(args, "command").trim().to_string();
    if command.is_empty() {
        command = ctx.project.test_command();
    }
    if command.is_empty() {
        return Err(Error::Tool(
            "No test command was detected for this project. Pass an explicit command to verify."
                .into(),
        ));
    }

    let timeout = args
        .get("timeout")
        .and_then(|v| v.as_u64())
        .unwrap_or(300);

    let result = ctx
        .shell
        .run(&command, ctx.workspace.root(), Duration::from_secs(timeout));
    let output = format!("$ {command}\n{}", result.formatted());
    if !result.succeeded() {
        return Err(Error::Tool(output));
    }
    Ok(output)
}

pub fn git(ctx: &ToolContext, args: &Value) -> Result<String> {
    Ok(ctx.git.run(&string_arg(args, "command"), 90))
}

// ----------------------------------------------------------------------
// Remote host (SSH bridge supplied by the host application)
// ----------------------------------------------------------------------
fn remote_call(request: serde_json::Value) -> Result<String> {
    let executor = crate::remote_executor().ok_or_else(|| {
        Error::Tool(
            "No remote host is configured for this build. Add an SSH connection in \
             Settings → SSH."
                .into(),
        )
    })?;
    executor.execute(&request.to_string())
}

pub fn ssh_exec(_ctx: &ToolContext, args: &Value) -> Result<String> {
    let command = string_arg(args, "command");
    if command.trim().is_empty() {
        return Err(Error::Tool("A command is required".into()));
    }
    let timeout = args
        .get("timeout")
        .and_then(|v| v.as_u64())
        .unwrap_or(120)
        .clamp(1, 600);
    remote_call(serde_json::json!({
        "op": "exec",
        "command": command,
        "timeout": timeout,
    }))
}

pub fn ssh_read_file(_ctx: &ToolContext, args: &Value) -> Result<String> {
    remote_call(serde_json::json!({
        "op": "read_file",
        "path": string_arg(args, "path"),
    }))
}

pub fn ssh_write_file(_ctx: &ToolContext, args: &Value) -> Result<String> {
    remote_call(serde_json::json!({
        "op": "write_file",
        "path": string_arg(args, "path"),
        "content": string_arg(args, "content"),
    }))
}

pub fn ssh_list_files(_ctx: &ToolContext, args: &Value) -> Result<String> {
    remote_call(serde_json::json!({
        "op": "list_files",
        "path": string_arg(args, "path"),
    }))
}

// ----------------------------------------------------------------------
// Web / image
// ----------------------------------------------------------------------
pub fn web_search(ctx: &ToolContext, args: &Value) -> Result<String> {
    let max_results = args
        .get("max_results")
        .and_then(|v| v.as_i64())
        .unwrap_or(5);
    let result = ctx
        .web_search
        .search(&string_arg(args, "query"), max_results);
    if result.starts_with("Web search ") || result == "Search query is empty" {
        return Err(Error::Tool(result));
    }
    Ok(result)
}

pub fn web_fetch(ctx: &ToolContext, args: &Value) -> Result<String> {
    let result = ctx.web_fetch.fetch(&string_arg(args, "url"));
    if result.starts_with("Web fetch error:")
        || result.starts_with("Web fetch blocked:")
        || result.starts_with("Web fetch timed out")
    {
        return Err(Error::Tool(result));
    }
    Ok(result)
}

pub fn generate_image(ctx: &ToolContext, args: &Value) -> Result<String> {
    let prompt = string_arg(args, "prompt");
    let size = {
        let size = string_arg(args, "size");
        if SUPPORTED_SIZES.contains(&size.as_str()) {
            size
        } else {
            "1024x1024".to_string()
        }
    };
    let count = args
        .get("count")
        .and_then(|v| v.as_u64())
        .unwrap_or(1)
        .clamp(1, 4) as u32;

    let results = ctx.image.generate(&prompt, &size, count)?;
    Ok(results
        .iter()
        .map(|result| result.message())
        .collect::<Vec<_>>()
        .join("\n\n"))
}

// ----------------------------------------------------------------------
// Task plan
// ----------------------------------------------------------------------
pub fn set_plan(ctx: &ToolContext, args: &Value) -> Result<String> {
    let tasks = ctx.require_tasks()?;
    let steps = args
        .get("steps")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let task = tasks.set_plan(&steps)?;
    ctx.emit(freenerf_core::AgentEvent::TaskUpdated { task: task.clone() });
    Ok(plan_message(&task))
}

pub fn update_step(ctx: &ToolContext, args: &Value) -> Result<String> {
    let tasks = ctx.require_tasks()?;
    let step = args
        .get("step")
        .and_then(|v| v.as_i64())
        .ok_or_else(|| Error::Tool("Step number must be an integer".into()))?;
    let status = string_arg(args, "status");
    let result = args.get("result").and_then(|v| v.as_str());
    let error = args.get("error").and_then(|v| v.as_str());

    let task = tasks.update_step(step, &status, result, error)?;
    ctx.emit(freenerf_core::AgentEvent::TaskUpdated { task: task.clone() });
    Ok(plan_message(&task))
}

pub fn finish(ctx: &ToolContext, args: &Value) -> Result<String> {
    let status = string_arg(args, "status");
    let summary = string_arg(args, "summary");
    let tasks = ctx.require_tasks()?;
    let task = tasks.finish(&status, &summary)?;
    ctx.emit(freenerf_core::AgentEvent::TaskCompleted {
        task_id: task["id"].as_str().unwrap_or("").to_string(),
        status: status.clone(),
        summary: summary.clone(),
    });
    Ok(format!("Task marked as {status}. Summary: {summary}"))
}

fn plan_message(task: &Value) -> String {
    let steps = task["steps"].as_array().cloned().unwrap_or_default();
    if steps.is_empty() {
        return "Plan updated".into();
    }
    let mut lines = vec![format!("Plan updated ({} steps):", steps.len())];
    for step in steps {
        let marker = match step["status"].as_str().unwrap_or("pending") {
            "completed" => "[x]",
            "skipped" => "[-]",
            "running" => "[>]",
            "failed" => "[!]",
            _ => "[ ]",
        };
        let position = step["position"].as_i64().unwrap_or(0) + 1;
        lines.push(format!(
            "{marker} {position}. {}",
            step["title"].as_str().unwrap_or("")
        ));
    }
    lines.join("\n")
}

// ----------------------------------------------------------------------
// Background processes
// ----------------------------------------------------------------------
pub fn task_start(ctx: &ToolContext, args: &Value) -> Result<String> {
    let result = ctx.background.start(&string_arg(args, "command"))?;
    if result == "Task limit reached" {
        return Err(Error::Tool(result));
    }
    Ok(format!("Task started: {result}"))
}

pub fn task_status(ctx: &ToolContext, args: &Value) -> Result<String> {
    let result = ctx.background.status(&string_arg(args, "process_id"))?;
    if let Some(message) = result.as_str() {
        return Err(Error::Tool(message.to_string()));
    }
    Ok(json_text(&result))
}

pub fn task_list(ctx: &ToolContext, args: &Value) -> Result<String> {
    let _ = args;
    let tasks = ctx.background.list()?;
    Ok(json_text(&Value::Array(tasks)))
}

pub fn task_stop(ctx: &ToolContext, args: &Value) -> Result<String> {
    let result = ctx.background.stop(&string_arg(args, "process_id"))?;
    if result == "Task not found" {
        return Err(Error::Tool(result));
    }
    Ok(result)
}
