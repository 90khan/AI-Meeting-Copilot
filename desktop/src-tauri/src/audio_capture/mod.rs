//! Native-audio capture contracts, lifecycle state, and bridge coordination.

#![allow(dead_code)] // The bridge is intentionally not wired to native capture in this task.

pub(crate) mod authorization;
pub(crate) mod bridge;
pub(crate) mod sources;
pub(crate) mod status;
pub(crate) mod swift_bridge;
pub(crate) mod types;
