// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "AudioCaptureBridge",
    platforms: [.macOS(.v15)],
    products: [
        .library(
            name: "AudioCaptureBridge",
            type: .static,
            targets: ["AudioCaptureBridge"]
        )
    ],
    targets: [
        .target(
            name: "AudioCaptureBridge",
            path: ".",
            sources: ["AudioCaptureBridge.swift"]
        )
    ]
)
