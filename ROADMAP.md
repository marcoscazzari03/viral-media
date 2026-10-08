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

## Da fare (8 ottobre)
- [x] Portale di controllo (fase 1, sola lettura): pagine Oggi e Coda su `dashboard.weborastudio.it`, login con
  password + Google Authenticator, dati letti dalle Data Tables via API REST di n8n (nessuna esecuzione in più).
  Da fare: record DNS, API key n8n, `app.setup` sul server. Poi fase 2 (Contenuti, Media, Factory, Workflow),
  fase 3 (Analytics, Costi), fase 4 (Impostazioni con scrittura e storico, Fonti, azioni sui Reel).
- [ ] Quando arriva l'approvazione dell'API TikTok: aggiungere TikTok anche al portale (views e follower nella
  pagina Oggi, link del video TikTok su ogni Reel, stato della pubblicazione, eventuali guadagni), oltre che
  ad Analytics (04) e al report giornaliero (05).
- [ ] Portale fase 2: pagine Contenuti (+ scheda Reel), Factory, Media, Workflow. Il portale legge anche la cartella
  dei media in sola lettura. Da verificare sul server dopo `update.sh`.
- [ ] Portale fase 3: pagina Analytics (views al giorno per social, follower, confronti per streamer / tema / voce /
  orario, top 10, costi di Claude e n8n). Per il costo esatto di Claude: far salvare al 02 token di input e output separati.
- [ ] Portale fase 4a: pagina Impostazioni (scrive `viral_config` via API, con validazione e storico). Poi 4b: Fonti
  (streamer) e azioni sui Reel (pubblica al prossimo slot, scarta, pulisci righe bloccate).
- [ ] Disco: `update.sh` ora cancella le immagini Docker vecchie; avviso nel portale sopra l'80%. Conservazione dei
  file portata a 14 giorni (`RETENTION_DAYS=14` nel `.env` del server); dopo qualche settimana valutare 30.
- [x] Webcam non riconosciuta sul Reel Jynxzi "pigmen" (testa bassa / girato verso il monitor: il volto non si
  vede in 3 fotogrammi su 6). Aggiunto un secondo controllo: il riquadro della webcam trovato dai suoi bordi in un
  angolo, valido solo se dentro c'è un volto grande in almeno 2 fotogrammi. Il pannello webcam ora è tagliato
  attorno al volto (prima al centro: testa tagliata). Testato su 12 clip (webcam, senza webcam, IRL). Da fare:
  aggiornare il server e controllare il layout dei prossimi Reel con webcam.

## Da fare (7 ottobre)
- [x] Mattina: svuotato `publish_force_post_key`; rimessi `factory_max_reels_per_day` = 3 e `publish_max_per_day` = 2.
- [x] Titolo spostato sotto la fascia alta di Instagram; layout webcam solo con webcam vera (falso positivo su Lacy IRL,
  Reel messo STALE). `has_burned_captions` = true anche per Stable Ronaldo.
- [x] Facebook (7 ottobre sera): workflow 07 attivo, primo Reel pubblicato via API (Jynxzi). Token della Pagina senza scadenza nella credenziale "US VIRAL - Facebook Page". Da fare: rigenerare i token (incollati in chat) e aggiungere le statistiche della Pagina al 04.
  Prima: la condivisione automatica Instagram → Pagina NON vale per i post pubblicati via API (Pagina ancora a
  0 post). Soluzione: workflow `07 - US VIRAL | Facebook Publisher` che pubblica lo stesso Reel sulla Pagina con
  l'API Reels delle Pagine (`/{page-id}/video_reels`), un'ora dopo Instagram, con avviso Telegram.
  Da fare prima, dall'utente: caso d'uso "Gestisci tutto sulla tua Pagina" nell'app Meta, token della Pagina
  (pages_show_list, pages_read_engagement, pages_manage_posts), credenziale n8n "US VIRAL - Facebook Page".
  Intanto: Reel di Jynxzi pubblicabile a mano da Meta Business Suite.
- [x] README aggiornato con il workflow 07.
- [x] Statistiche multi-social: 04 legge anche views / like / commenti degli Short, iscritti YouTube e collega da solo
  gli Short caricati a mano (niente tabelle a mano); il report delle 20:20 mostra views 24h per social, variazione
  follower / iscritti e Reel top.
- [ ] Stasera, dopo il token della Pagina: statistiche Facebook in 04 (views Reel, follower, abbinamento dei Reel
  condivisi a mano) e riga "💰 Incassato" nel report con i guadagni veri di YouTube (`estimatedRevenue`, YouTube
  Analytics API, permesso OAuth di sola lettura ricavi) e Facebook (metrica guadagni delle Page Insights, da
  verificare sull'API). Oggi $0: nessun canale è ancora monetizzato. Niente stime views × RPM.

## Fatto il 7 ottobre (sera)
- [x] Facebook automatico (workflow 07) + statistiche della Pagina, follower e guadagni reali nel 04.
- [x] Report delle 20:20 a sezioni: pubblicazioni, views 24h per social, top contenuto (tutti i social),
  💰 guadagni reali (Facebook via API; Instagram/YouTube/TikTok senza dato), perché sotto target, pipeline.
- [x] 5 temi visivi (giallo, rosso, blu, verde, viola) con rotazione nella Factory; copertine con badge e logo.
- [x] Abbinamento automatico degli Short e dei Reel della Pagina caricati/condivisi a mano.
- [x] Esperimento "nascondere i sottotitoli dello streamer" (fascia fissa e riconoscimento automatico): scartato,
  risultato peggiore dei sottotitoli originali. Codice spento sul server (`hide_band`, `hide_subs`), non usato.

## Prossimi passi
- [ ] Sicurezza: rigenerare i token Facebook (incollati in chat), cambiare `API_TOKEN` del server e il segreto
  Sandbox TikTok, poi repo privato (prima deploy key di sola lettura sul server).
- [ ] TikTok: all'approvazione dell'app chiavi di produzione + pubblicazione automatica da n8n.
- [ ] YouTube: all'approvazione dell'audit `yt_enabled` = 1, `yt_privacy` = public, attivare il 06.
- [ ] Tra qualche giorno, con i dati: confronto voce sì/no, temi e streamer; priorità delle sorgenti che si
  aggiustano da sole (`learned_weight`); verificare i 12 nuovi streamer; valutare 3 Reel al giorno.
- [ ] Avviso Telegram ~10 giorni prima della scadenza del token Instagram (circa 4 dicembre).
- [ ] Ripulire i Reel di prova ("forced") e le righe rimaste a metà nelle tabelle.

## Blocco Meta (6 ottobre)
- [x] Pubblicazione Instagram via API bloccata ("API access blocked", 17:05): risolta completando la verifica
  dell'account Facebook. Primo Reel pubblicato via API il 7 ottobre all'01:06 (Jynxzi, layout webcam).
- [ ] Valori temporanei per far lavorare la Factory a pubblicazione spenta: `factory_max_reels_per_day` = 6,
  `publish_max_per_day` = 5. **Alla ripartenza rimettere 3 e 2**, poi `publish_enabled` = 1 dopo un solo test.
- [ ] Il Reel CaseOh "lol" è in PUBLISH_FAILED (vecchio stile): non ripubblicarlo.

## Più avanti
- [ ] Rendere il repo GitHub privato: prima deploy key di sola lettura sul server (altrimenti `update.sh` non riesce più a fare `git pull`), poi Settings → Change visibility.
- Nota esecuzioni n8n (limite 2.500/mese, condiviso con tutti i workflow): 01 e 02 ogni 2 ore, 03 alle 11-12-15-16-19-20 NY, 04 ogni 6 ore, 05 una volta al giorno, 06 alle 12-16-20 NY. Circa 40 esecuzioni al giorno (≈1.250 al mese) con YouTube attivo. **Se cambi `publish_slots_et`, vanno cambiati anche gli orari dei trigger di 03 e 06.**
- [x] Più streamer Twitch tra le fonti: +12 il 7 ottobre (xqc, summit1g, tarik, yourragegaming, jasontheween, silky, agent00, extraemily, emiru, sketch, nmplol, zoil), da verificare dopo qualche run del Discovery.
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
