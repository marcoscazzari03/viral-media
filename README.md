# US Viral Media

Automated US-focused viral content system built on n8n.

| Component | Where |
|---|---|
| `00 - US VIRAL \| Error Handler` | n8n Cloud |
| `01 - US VIRAL \| Discovery Engine` | n8n Cloud |
| `02 - US VIRAL \| Reel Factory` | n8n Cloud (planned) + [`media-worker/`](media-worker/) |
| `03 - US VIRAL \| Publisher` | n8n Cloud (planned) |
| `04 - US VIRAL \| Analytics` | n8n Cloud (planned) |

`media-worker/` is the self-hosted service (FFmpeg, faster-whisper, Piper TTS) used by the Reel Factory.
