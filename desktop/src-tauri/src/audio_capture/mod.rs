//! Native-audio capture contracts, lifecycle state, and bridge coordination.

#![allow(dead_code)] // The bridge is intentionally not wired to native capture in this task.

pub(crate) mod authorization;
pub(crate) mod bridge;
pub(crate) mod chunker;
pub(crate) mod commands;
pub(crate) mod coordinator;
pub(crate) mod mixer;
pub(crate) mod pcm16;
pub(crate) mod processing;
pub(crate) mod resampler;
pub(crate) mod sender;
pub(crate) mod sources;
pub(crate) mod status;
pub(crate) mod swift_bridge;
pub(crate) mod types;
pub(crate) mod wav;
