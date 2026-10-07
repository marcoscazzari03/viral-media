# US VIRAL — Roadmap

Promemoria delle decisioni prese. Niente segreti in questo file (repo pubblico).

## Fatto
- [x] Scala velocità Twitch (`twitch_vph_log_ceil` 3 → 2.3) e `factory_min_views` 500 → 250.
- [x] Clip ELIGIBLE "appiccicose" (picco confermato ≥ 60 e punteggio ancora ≥ 50), finestra Twitch 72 h (scadenza 96 h), minimo 250 views già nel Discovery.

## Domani, dopo il controllo della prima pubblicazione automatica
- [x] Telegram: resoconto serale (05 Daily Report, 20:20 New York), avvisi di errore (00), "Reel pubblicato / fallito" (03).
- [x] Stile ispirato a @jakeetimberlake: frase meme fissa (`factory_text_style`), voce AI al 50% per A/B test
  (`factory_voice_ratio`, colonna `style_variant`), clip fino a 60 s (`factory_max_segment_s`), sottotitoli del
  parlato nei Reel senza voce.
- [x] Stile "pop" della frase meme (6 ottobre): font Montserrat, testo inclinato, parola chiave in giallo scelta da
  Claude (`meme_accent`), sottolineatura e scintille, @handle sottolineato. `style_variant` = `memepop+voice` /
  `memepop+novoice` (prima `meme+...`).
- [x] Copertina Instagram disegnata (6 ottobre): fotogramma del momento più forte a tutto schermo, frase pop,
  nome dello streamer, @handle; tutto dentro l'area 3:4 visibile nella griglia del profilo.
- [ ] Dopo 1-2 settimane: confrontare le varianti (`memepop+voice` / `memepop+novoice`, e i vecchi `meme+...`)
  con 04 Analytics e fissare la percentuale di voce.
- [x] Layout "webcam sopra, gioco sotto" automatico quando nella clip c'è una webcam (6 ottobre).
- [ ] Varianti da testare dopo: video a schermo pieno (lati tagliati). Più avanti: nome del gioco nella copertina ("ASMONGOLD x WOW").

## Dopo 1-2 giorni di pubblicazioni senza errori
- [ ] Fonte Kick (Adin Ross, xQc e altri): parte di ricerca nel Discovery (il worker scarica già le clip Kick).
- [ ] Fonte YouTube (IShowSpeed e live): attivare il ramo YouTube nella Factory, con più cautela sui diritti.

## TikTok (@viralstreamersdaily)
- [x] App "US Viral Publisher" su developers.tiktok.com, dominio media.weborastudio.it verificato, pagine legali.
- [x] Pagina di pubblicazione https://media.weborastudio.it/panel (login TikTok, post diretto / bozza) testata in Sandbox.
- [ ] Revisione TikTok inviata con video demo: attendere approvazione.
- [ ] Dopo l'approvazione: chiavi Production nel `.env`, ricollegare l'account dal pannello, **account TikTok di nuovo pubblico**.
- [ ] Workflow n8n per pubblicare su TikTok in automatico (stesso endpoint `/tiktok/post`).
- [ ] Rigenerare `API_TOKEN` del server e secret TikTok Sandbox (sono passati in chat).

## Da fare (7 ottobre)
- [x] Mattina: svuotato `publish_force_post_key`; rimessi `factory_max_reels_per_day` = 3 e `publish_max_per_day` = 2.
- [x] Titolo spostato sotto la fascia alta di Instagram; layout webcam solo con webcam vera (falso positivo su Lacy IRL,
  Reel messo STALE). `has_burned_captions` = true anche per Stable Ronaldo.
- [ ] Facebook: la condivisione automatica Instagram → Pagina NON vale per i post pubblicati via API (Pagina ancora a
  0 post). Soluzione: workflow `07 - US VIRAL | Facebook Publisher` che pubblica lo stesso Reel sulla Pagina con
  l'API Reels delle Pagine (`/{page-id}/video_reels`), un'ora dopo Instagram, con avviso Telegram.
  Da fare prima, dall'utente: caso d'uso "Gestisci tutto sulla tua Pagina" nell'app Meta, token della Pagina
  (pages_show_list, pages_read_engagement, pages_manage_posts), credenziale n8n "US VIRAL - Facebook Page".
  Intanto: Reel di Jynxzi pubblicabile a mano da Meta Business Suite.
- [ ] Il README dice ancora "Facebook via Meta auto-share": aggiornare quando c'è il workflow 07.

## Blocco Meta (6 ottobre)
- [x] Pubblicazione Instagram via API bloccata ("API access blocked", 17:05): risolta completando la verifica
  dell'account Facebook. Primo Reel pubblicato via API il 7 ottobre all'01:06 (Jynxzi, layout webcam).
- [ ] Valori temporanei per far lavorare la Factory a pubblicazione spenta: `factory_max_reels_per_day` = 6,
  `publish_max_per_day` = 5. **Alla ripartenza rimettere 3 e 2**, poi `publish_enabled` = 1 dopo un solo test.
- [ ] Il Reel CaseOh "lol" è in PUBLISH_FAILED (vecchio stile): non ripubblicarlo.

## Più avanti
- [ ] Rendere il repo GitHub privato: prima deploy key di sola lettura sul server (altrimenti `update.sh` non riesce più a fare `git pull`), poi Settings → Change visibility.
- Nota esecuzioni n8n (limite 2.500/mese, condiviso con tutti i workflow): 01 e 02 ogni 2 ore, 03 alle 11-12-15-16-19-20 NY, 04 ogni 6 ore, 05 una volta al giorno, 06 alle 12-16-20 NY. Circa 40 esecuzioni al giorno (≈1.250 al mese) con YouTube attivo. **Se cambi `publish_slots_et`, vanno cambiati anche gli orari dei trigger di 03 e 06.**
- [ ] Più streamer Twitch tra le fonti (oggi ~7: poche clip forti).
- [ ] Ripulire i post di test rimasti a metà e la clip bloccata in PROCESSING.
- [x] 2 Reel al giorno (`publish_max_per_day` = 2).
- [x] Pubblicazione su Pagina Facebook (condivisione automatica Meta; da verificare sui primi Reel).
- [x] YouTube Shorts: canale creato, workflow 06 testato in privato, audit API inviato (6 ottobre).
- [ ] Dopo l'approvazione dell'audit YouTube: `yt_enabled` = 1, `yt_privacy` = public, attivare il workflow 06.
- [x] Svuotato `yt_force_post_key` in `viral_config`.
- [ ] Avviso scadenza token Instagram (intorno al 4 dicembre).
- [ ] Learning loop quando ci sono 5-10 Reel pubblicati.
- [x] `has_burned_captions` = true per Asmongold (`twitch:zackrawrr`) in `viral_sources`: i suoi stream hanno già i sottotitoli.
- [ ] Rilevamento automatico dei sottotitoli già impressi nelle clip.
- [ ] Sistema multi-pagina (colonna `page`) dopo 2-3 settimane; seconda nicchia da scegliere (gaming/FPS o Tech in formato notizia).
