# Public beta 0.2.0-beta.1

User-approved scope: publish source to the existing GitHub repository and provide a Windows installer for feedback. Public builds support only DeepSeek API, using each user's own key. Development builds may retain their local bridge.

1. Add an immutable public distribution marker. Validate DeepSeek provider and official HTTPS endpoint at configuration loading, client construction, and first-use writes. Public builds must never discover local account credentials. Remove bridge code from the frozen bundle and bridge controls from rendered public pages.
2. Build a fresh independent public payload, without the owner's launcher, config, secrets, chats or update registry. Make runtime paths work on machines without D drive. Distinguish public update compatibility from development releases.
3. Package a self-contained Windows x64 installer, creating separate per-user data, a user-chosen login password, session secret and launcher. No default credentials or embedded API keys. Refuse overwriting existing data or program directories. Include feedback and installation instructions.
4. Verify provider restrictions, existing regression tests, clean installation and packaged pages. Audit tracked source and payload for private files. Publish source and prerelease installer with SHA256 to GitHub without force-pushing.

Artifacts: D:\Cache\QQDigestPublicBeta-2026-10-06; D:\Apps\QQDigestDesktop-0.2.0-beta.1; D:\Downloads\QQDigest-0.2.0-beta.1-Windows-x64-Setup.exe.

Local validation completed: 884 collected tests, 883 passed and one platform skip; actual installer password/login/key-save checks passed. Frozen PYZ excludes the bridge; compiled public hook retains restrictions with the JSON marker removed. Desktop and narrow-screen pages checked in the browser with no console errors. Reviewed fixes cover private data directory containment and cross-session installer serialization. 518 historical text blobs scanned without likely tokens or tracked private data. GitHub publication uses normal Git authorization and a repository Actions workflow; no local credentials are extracted.
