# 0.2.2 navigation patch — 2026-10-23

Scope: target-scoped DOM invalidation for UI actions; Android full/simple view switches, standard tab/disclosure navigation, and three existing viewer/menu handlers. Settings/auth, generation, file operations and edit permission guards remain in place.

Passed: 17 JavaScript policy/transport regression tests, 8 loader tests, 11 screen integration tests, 25 repair/downloader tests. Background mutations no longer expire an unchanged Quick button; target/ancestor changes, covered controls, changed handlers, trusted user input and protected screens still reject actions. Live tablet behavior after installing 0.2.2 remains unverified.

Payload SHA-256: `68b384f424717f899565bbd132fa86a1b3d9c64d1edc47604d22a7f53ebedd00`.

The historical 0.2.1 verification below applies to that release, not a rerun of every test for 0.2.2.

---

# 0.2.1 repair verification — 2026-10-23

This release repairs extension initialization and reload failures. It does not replace the APK or change generation pricing, credentials, or permission choices.

## Source references

- Host: DNT-LAB/NAIA2.0 at `6e97c7f4f37e495ad21657ed29d28dd9993a808e`.
- Android tools: `909ecb69385f42c924077390afe3285a9366cb25`.
- ChatGPT connection: `fafe03b4e5546d21ea85e1f92e4f855c8754da3f`.
- Storage picker: `5e6a750bf15a72006d4c95d13c6178e65b2bcc22`.
- Repair payload SHA-256: `c1ea165ce262cb44dd8138d95df2bfe1077c1f88a58e9c98526f7a19c83ad1c1`.

## Checks completed

- 84 Android tools Python tests, 12 screen-agent JavaScript tests, 8 legacy extension tests, 25 repair/downloader tests, and 7 integration regressions passed. The final defensive loader guard was additionally checked with all 8 loader tests.
- Reproduced the three reported failures using shipped source fixtures and the real host ExtensionManager: unexpected `screen_service`, duplicate ChatGPT bridge registration, and storage panel revision mismatch.
- The actual bundled repair restored all three extensions in the same Python process, with versions 0.2.1 / 0.1.2 / 0.1.1. Mixed repeated reloads rendered each UI addition once.
- Verified byte-for-byte preservation of extension permission configuration, settings, offline dummy OAuth state, and offline dummy pairing state. Previously blocked ChatGPT v1 stayed blocked. The old updater remained loaded without applying anything.
- Verified fresh helper entry plus already-applied payload can explicitly reload reviewed extensions again.
- Exercised source validation, archive constraints, rollback, interrupted transaction recovery, immutable-commit downloads, and the host's non-reentrant manager lock.
- Repeated payload builds produced identical bytes. No real login, model inference, paid image generation, or device command was used in these tests.

## Limits

This is local host and source-level verification. The user's Galaxy/WebView has not run this repair yet. Managed browser preview was unavailable. The separately installed convenience extension 0.4.1 was not available in the pinned host source and was not included in the integration run. Live OAuth, relay connectivity, and the device UI must be checked after applying the repair; passing these tests does not establish those outcomes.
