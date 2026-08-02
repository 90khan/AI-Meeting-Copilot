//! Redaction for internal sidecar diagnostics.

const SENSITIVE_ENVIRONMENT_NAMES: [&str; 1] = ["AI_MEETING_COPILOT_SIDECAR_AUTH_TOKEN"];

/// Redact the launch token, sensitive environment assignments, and token-like words.
pub fn redact_diagnostic(input: &str, token: &str) -> String {
    let mut redacted = input.replace(token, "[REDACTED]");
    for name in SENSITIVE_ENVIRONMENT_NAMES {
        redacted = redact_assignment(&redacted, name);
    }
    redacted
        .split_whitespace()
        .map(redact_token_like_word)
        .collect::<Vec<_>>()
        .join(" ")
}

fn redact_assignment(input: &str, name: &str) -> String {
    let marker = format!("{name}=");
    let mut remaining = input;
    let mut result = String::new();
    while let Some(index) = remaining.find(&marker) {
        let (prefix, after_prefix) = remaining.split_at(index + marker.len());
        result.push_str(prefix);
        result.push_str("[REDACTED]");
        let value_end = after_prefix
            .find(char::is_whitespace)
            .unwrap_or(after_prefix.len());
        remaining = &after_prefix[value_end..];
    }
    result.push_str(remaining);
    result
}

fn redact_token_like_word(word: &str) -> String {
    let is_url_safe = word
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_');
    if is_url_safe && word.len() >= 32 {
        "[REDACTED]".to_owned()
    } else {
        word.to_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::redact_diagnostic;

    #[test]
    fn redacts_tokens_and_sensitive_environment_assignments() {
        let token = "A2345678901234567890123456789012";
        let diagnostic = format!("AI_MEETING_COPILOT_SIDECAR_AUTH_TOKEN={token} token {token}");

        let redacted = redact_diagnostic(&diagnostic, token);

        assert!(!redacted.contains(token));
        assert!(redacted.contains("AI_MEETING_COPILOT_SIDECAR_AUTH_TOKEN=[REDACTED]"));
    }
}
