# MoziToolKit

**MoziToolKit** is a Blender 5.0+ extension for Minecraft world importing, resource pack processing, `.mtkscene` exchange, PBR shader assembly, and UV/mesh editing.

[中文文档 (Chinese README)](README.zh.md)

---

## Offline-First & External Dependencies

All core features—including Anvil save (`.mca`) importing, resource/PBR pack compilation, `.mtkscene` import/export, voxel point-cloud editing, and UV/pixel-extrusion tools—operate **100% offline** with no external account, API key, or cloud service required.

### Open-Source Ecosystem & Source Repositories

- **Core Native Engine (`libmozitoolkit`)** — Rust core library (`libmtk_py` / `libmtk_blender_dna`) handling meshing, face culling, atlas packing, biome palettes, and Anvil parsing under `GPL-3.0-or-later`:
  - GitHub: <https://github.com/MoziToolKit/libmozitoolkit>
- **Live Sync Companion Mod (`yefira`, Optional)** — Open-source Minecraft mod (Fabric / Forge / NeoForge) that streams local worlds via local IPC (`ipc://`, zero network ports) or self-hosted worlds over WebSocket (`ws://127.0.0.1:21924` by default):
  - GitHub: <https://github.com/MoziToolKit/yefira>

---

## Permissions

In accordance with Blender Extensions guidelines:

- **`files`**: Reads local Minecraft resource packs, JARs, and world saves; writes precompiled texture atlases, caches, and `.mtkscene` packages.
- **`network`**: Optional. Connects only to a user-configured local or self-hosted Live Sync WebSocket server (`yefira`) and optionally fetches Minecraft player head skins when **Allow Online Access** is enabled in Blender (`Preferences > System > Network`). All network features are disabled when Blender is offline.

---

## License

Licensed under the **GNU General Public License v3.0 or later (`GPL-3.0-or-later`)**. See [LICENSE](LICENSE) for details.
