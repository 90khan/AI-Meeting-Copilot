//! Strict parsing for the sidecar's single stdout readiness line.

use serde::Deserialize;
use thiserror::Error;

pub const READINESS_VERSION: u8 = 1;
pub const PROTOCOL_VERSION: u8 = 1;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Readiness {
    pub host: String,
    pub port: u16,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ReadinessError {
    #[error("sidecar readiness output is invalid")]
    Invalid,
}

#[derive(Debug, Deserialize)]
struct ReadinessPayload {
    #[serde(rename = "type")]
    message_type: String,
    version: u8,
    host: String,
    port: u16,
    protocol_version: u8,
}

/// Parse and validate the fixed V1 readiness payload without retaining raw stdout.
pub fn parse_readiness(line: &str) -> Result<Readiness, ReadinessError> {
    let payload: ReadinessPayload =
        serde_json::from_str(line).map_err(|_| ReadinessError::Invalid)?;
    if payload.message_type != "ready"
        || payload.version != READINESS_VERSION
        || payload.protocol_version != PROTOCOL_VERSION
        || payload.host != "127.0.0.1"
        || payload.port == 0
    {
        return Err(ReadinessError::Invalid);
    }

    Ok(Readiness {
        host: payload.host,
        port: payload.port,
    })
}

#[cfg(test)]
mod tests {
    use super::{parse_readiness, Readiness, ReadinessError};

    #[test]
    fn parses_a_valid_readiness_payload() {
        assert_eq!(
            parse_readiness(
                r#"{"type":"ready","version":1,"host":"127.0.0.1","port":51842,"protocol_version":1}"#,
            ),
            Ok(Readiness {
                host: "127.0.0.1".to_owned(),
                port: 51842,
            }),
        );
    }

    #[test]
    fn rejects_invalid_readiness_fields() {
        for payload in [
            r#"{"type":"other","version":1,"host":"127.0.0.1","port":1,"protocol_version":1}"#,
            r#"{"type":"ready","version":2,"host":"127.0.0.1","port":1,"protocol_version":1}"#,
            r#"{"type":"ready","version":1,"host":"127.0.0.1","port":1,"protocol_version":2}"#,
            r#"{"type":"ready","version":1,"host":"localhost","port":1,"protocol_version":1}"#,
            r#"{"type":"ready","version":1,"host":"127.0.0.1","port":0,"protocol_version":1}"#,
        ] {
            assert_eq!(parse_readiness(payload), Err(ReadinessError::Invalid));
        }
    }
}
