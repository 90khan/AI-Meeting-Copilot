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
            name: "CaptureSourcesObjC",
            path: "Sources/CaptureSourcesObjC",
            publicHeadersPath: "include",
            linkerSettings: [
                .linkedFramework("AVFoundation"),
                .linkedFramework("ScreenCaptureKit")
            ]
        ),
        .target(
            name: "AudioCaptureBridge",
            dependencies: ["CaptureSourcesObjC"],
            path: ".",
            exclude: ["Sources"],
            sources: [
                "AudioAuthorization.swift",
                "AudioCaptureBridge.swift",
                "CaptureSources.swift",
                "ScreenCaptureEngine.swift"
            ]
        )
    ]
)
