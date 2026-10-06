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
- [ ] Dopo 1-2 settimane: confrontare `meme+voice` vs `meme+novoice` con 04 Analytics e fissare la percentuale.

## Dopo 1-2 giorni di pubblicazioni senza errori
- [ ] Fonte Kick (Adin Ross, xQc e altri): parte di ricerca nel Discovery (il worker scarica già le clip Kick).
- [ ] Fonte YouTube (IShowSpeed e live): attivare il ramo YouTube nella Factory, con più cautela sui diritti.
- [ ] Layout gaming: facecam sopra, gioco sotto.

## Più avanti
- [ ] Più streamer Twitch tra le fonti (oggi ~7: poche clip forti).
- [ ] Ripulire i post di test rimasti a metà e la clip bloccata in PROCESSING.
- [x] 2 Reel al giorno (`publish_max_per_day` = 2).
- [ ] Pubblicazione su Pagina Facebook.
- [ ] YouTube Shorts: creare il canale e chiedere subito l'audit API.
- [ ] Avviso scadenza token Instagram (intorno al 4 dicembre).
- [ ] Learning loop quando ci sono 5-10 Reel pubblicati.
- [ ] Rilevamento automatico dei sottotitoli già impressi nelle clip.
- [ ] Sistema multi-pagina (colonna `page`) dopo 2-3 settimane; seconda nicchia da scegliere (gaming/FPS o Tech in formato notizia).
