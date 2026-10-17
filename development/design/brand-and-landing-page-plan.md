# Runestra: plan za zaključek celostne podobe in pristajalne strani

**Datum:** 12. oktober 2026<br>
**Status:** digitalna CGP in lokalna pristajalna stran sta izdelani za pregled; javna objava ni izvedena<br>
**Potrjena smer:** temna tehnična stran, Signalni tok, z mint poudarkom<br>
**Potrjeni cilj:** strokovni ugled avtorja in uporabna odprta izdaja<br>
**Preverjeno izhodišče:** repozitorij pri `567c460`; izbrana postavitev strani 2; paket CGP v [Runestra-product/visual-identity](../../../Runestra-product/visual-identity/README.md); lokalna stran v [website/](../../website/)

## 1. Končni rezultat

Zaključek pomeni izbran in tehnično urejen logotip, uporaben sistem vizualne identitete, preverjeno pristajalno stran ter paket datotek za nadaljnjo uporabo. Lep mockup in ustvarjena slika logotipa ne zadostujeta.

Znamka mora biti prepoznavna tudi brez mint barve. Stran mora tehničnemu obiskovalcu jasno povedati, kaj Runestra izvaja, kje se delo izvaja in kako začne. Strokovni ugled podpreta resnični produkt in avtorstvo, ne velika osebna predstavitev.

| Dostava | Kaj pomeni dokončano |
|---|---|
| Logotip | Izbran piktogram in besedna znamka; urejen vektorski izvor, različice in preverba majhnih velikosti |
| Celostna podoba | Barve, pisave, razmiki, komponente, pravila ikon in slik ter primeri uporabe so dokumentirani |
| Pristajalna stran | Odzivna angleška stran z dejanskimi podatki, delujočimi povezavami in uporabnim začetnim korakom |
| Paket za uporabo | Logo, favicon, socialna slika, osnovna predstavitvena predloga in priročnik imajo jasna imena ter verzijo |
| Javna objava | Gostovanje, domena in HTTPS so preverjeni; objavljena različica prestane kratek ponovni pregled |

Dokončana identiteta, preverjena lokalna stran in javna objava so tri ločena stanja. Pripravljenost strani ni dokaz zaključene javne izdaje produkta.

## 2. Izhodišča, ki jih ne odpiramo znova

- Ime je **Runestra**, načrtovani naslov je `runestra.app`; lastništvo in stanje DNS pred objavo preverimo.
- Publika so senior in staff engineerji, tech leadi ter engineering managerji.
- Osnova je Signalni tok: temne nevtralne površine, čista sans-serif hierarhija in omejen mint poudarek.
- Logotip je samostojen abstrakten piktogram; brez črke R, rune, povezanih krogov, razcepa Y ali osrednje puščice.
- Avtorstvo je diskretno: **Created by Sašo Sečnjak**. Povezavo do profila določimo pred končnim besedilom.
- Aplikacija ohrani svetlo in temno temo ter semantiko operativnih statusov. Temna pristajalna stran ne zahteva spremembe tega vedenja.
- Javna stran je v angleščini. Delovni plan ostane v slovenščini, koda in komentarji v angleščini.

Aktualni viri so [README](../../README.md), [Getting started](../../docs/getting-started.md), [Apache-2.0 licenca](../../LICENSE) in [sistem vmesnika](../design-system.md). Prejšnji materiali vsebujejo stare povezave, MIT in starejši setup. Ne kopiramo jih v novo stran brez preverjanja.

## 3. Faza A: zaključimo brief in vsebino

**Dostava:** kratek brief in osnutek celotnega besedila strani, vključno z viri za trditve.

1. Ohranimo jedro sporočila: **AI agent workflows, defined in Git. Run on your own runners.**
2. Podnaslov konkretno poveže agente, shell preverjanja, eksplicitne objave in pregled rezultatov. Odobritev opiše pripravljeni coding tok, ne vseh poljubnih workflowov.
3. Izberemo primarni naslednji korak obiskovalca: začeti lokalni primer. GitHub in dokumentacija sta podporni poti.
4. Zabeležimo pri vsakem prikazanem primeru, kaj je implementirano, kaj je dejansko preizkušeno in kaj še čaka na sprejem.
5. Pripravimo javno varen prikaz runa in odobritve iz testnega projekta. Zasebni računi, endpointi, tokeni in interne naloge ne sodijo na slike.

**Kriterij:** stran razloži rezultat pred arhitekturo; nobena trditev se ne opira samo na načrt ali generirani PNG. Ta faza in priprava logo konceptov lahko potekata vzporedno.

## 4. Faza B: definiramo in izberemo logotip

Pripravimo **tri oblikovno različne koncepte**, ne treh barvnih različic istega znaka. Ta plan razširi prejšnji brief z dveh na tri začetne koncepte, da je izbira jasnejša. Vsi ostanejo znotraj potrjene tehnične smeri.

### Iztočnice za tri koncepte

| Koncept | Oblikovalska ideja | Glavno tveganje |
|---|---|---|
| Plasti | Zamaknjeni ploskvi in jasna negativna reža; namig na različico in pregled | Preveč podoben generičnemu stack oziroma layers simbolu |
| Prehod | Dve kompaktni obliki, med katerima nastane razločen izrez; namig na nadzorovan prehod | Lahko postane črka, oklepaj ali puščica |
| Ritem | Uravnotežene geometrijske mase z eno kontrolirano prekinitvijo; namig na zaporedje | Lahko deluje kot dekoracija brez prepoznavne silhuete |

Tabela beleži začetne oblikovalske hipoteze. Izbrana smer in njena dodelava sta opisani spodaj.

### Primerljiv prikaz

Vsak koncept pokažemo enobarvno na svetli in temni podlagi, skupaj z napisom Runestra ter v velikostih 16, 24, 32 in 48 px. Dodamo isti kontekst glave strani, aplikacije in GitHub README. Najprej ocenimo silhueto, šele nato barvo.

| Merilo izbire | Teža |
|---|---|
| Razlikovalnost in pregled vidne podobnosti | 30 % |
| Berljivost pri 16 in 24 px | 25 % |
| Ujemanje s tehničnim produktom | 20 % |
| Samostojna enobarvna uporaba | 15 % |
| Ujemanje z besedno znamko | 10 % |

Vsako merilo ocenimo od 1 do 5 in podamo eno priporočilo z razlogi. Podoben znak ali neberljiv favicon je razlog za izločitev, tudi če je skupna ocena visoka. Pregled podobnosti ne pomeni pravne potrditve znamke.

**Odločitev uporabnika, 12. oktober 2026:** izbral je tretjo smer, tri enako debele, rahlo nagnjene črte z zanko, ki nakazuje ponavljajoč se workflow. Za športni videz je treba poskrbeti z asimetričnim potekom zanke in preverbo pri majhnih velikostih. Sledi največ en dodaten ciljan krog dodelave. Če znak pri majhni velikosti ne deluje, ga poenostavimo ali zavrnemo; ne rešujemo ga samo z večjim prikazom.

### Digitalni vektorski paket, pripravljen za pregled

- Uredimo geometrijo, optično poravnavo, negativni prostor in razmerje z besedno znamko.
- Določimo primarno vodoravno sestavo, samostojen znak in samostojen napis. Navpična sestava je potrebna le, če ima konkretno uporabo.
- Pripravimo osnovno, enobarvno in inverzno različico ter morebitno poenostavitev za favicon.
- Določimo najmanjšo velikost, varnostni prostor, dovoljene podlage in primere napačne uporabe.
- Dostavimo urejen vektorski izvor in izvoze SVG, PDF, PNG ter favicon ICO/SVG. Besedna znamka za distribucijo ne sme biti odvisna od manjkajoče pisave.
- Zabeležimo izvor, uporabljena orodja in dovoljenja za pisavo ter druge elemente. Raster ideja je izhodišče, ne končni master.

**Status dostave:** uporabnik je izbral zadnji koncept, znak sem na novo prerisal v vektorski obliki, z jasno povratno zanko na desni. Pripravljeni so mint/svetla/temna različica, vodoravni lockup, favicon SVG/ICO in PNG izvozi, posodobljeni pa so tudi engine, pristajalna stran in Open Graph slika. Pri 16 px uporabimo favicon; samostojni znak je namenjen velikostim od 24 px. Neodvisna pravna preverba podobnosti ni bila izvedena.

## 5. Faza C: zaključimo celostno podobo

**Dostava:** priročnik za digitalno uporabo in paket osnovnih elementov. Prvi osnutek je v [brand/brand-guidelines.md](brand/brand-guidelines.md); barve in majhne velikosti so še v preverjanju. Prva izdaja CGP pokrije produkt, dokumentacijo in javno predstavitev.

| Področje | Določimo |
|---|---|
| Barve | Temne nevtralne površine, mint znamke, besedilo in obrobe; vlogo ter preverjen kontrast vsake kombinacije |
| Tipografija | Družino za naslove in besedilo, monospace za ukaze; velikosti, teže in fallback |
| Postavitev | Širino vsebine, mrežo, lestvico razmikov, zaobljenost, pravila gostote |
| Komponente strani | Primarni in sekundarni CTA, link, code block, oznako predizdaje, FAQ in prikaz produkta |
| Ikone | Enoten slog za funkcijske ikone; logotip ostane ločen zaščitni znak |
| Slike | Resnični posnetki, način obrezovanja, podlage, opisi in oznake različice |
| Besedilo | Jedrnati naslovi, konkretne zmožnosti in omejitve, diskretno avtorstvo |
| Gibanje | Samo funkcijsko; spoštuje zmanjšano gibanje, brez animacije kot pogoja za razumevanje |

Mint ni nadomestilo za vse operativne barve. Statusi aplikacije ostanejo vezani na obstoječe semantične tokene. Nova marketinška barva zahteva svojo jasno vlogo.

Privzeto uporabimo kakovostno sans-serif rešitev z dovoljenjem za spletno uporabo in jasno določeno distribucijo. Sistemska pisava ostane veljavna možnost. Serifne ali dodatne dekorativne družine v tej smeri niso potrebne.

Primeri uporabe: desktop in mobilna glava strani, glava aplikacije v obeh temah, GitHub README, favicon, Open Graph slika ter naslovna predloga za tehnično predstavitev. Poslovne vizitke, tiskovine in obsežen brand book niso pogoj te dostave.

**Kriterij:** drug izvajalec iz priročnika pravilno sestavi stran ali osnovno predstavitev brez ugibanja o barvah, velikostih in uporabi logotipa.

## 6. Faza D: zasnujemo pristajalno stran

Pripravimo **tri primerljive vizualne postavitve** v isti potrjeni smeri, z istim besedilom in istimi resničnimi prikazi. Razlikujejo se v hierarhiji uvoda, razmerju med ukazi in prikazom produkta ter ritmu odsekov. To ni ponovno izbiranje med temno in svetlo znamko.

Pokažemo desktop ter mobilni uvod in ključne spodnje odseke. Podamo priporočilo; uporabnik izbere postavitev pred začetkom izdelave. Logotip je v tej fazi že izbran. Generirana študija se jasno loči od screenshota produkta.

### Vsebina ene strani

| Odsek | Vsebina in namen |
|---|---|
| Navigacija | Znak in Runestra, Documentation, GitHub ter glavni CTA |
| Uvod | Naslov, konkreten podnaslov, Get started in View on GitHub; jasno stanje vira oziroma izdaje |
| Quick start | Kratek copy-paste blok, predpogoji, rezultat prvega zagona in povezava do navodil |
| Produktni dokaz | Pravi prikaz runa ali odobritve z razlago, kaj uporabnik vidi |
| Kako deluje | Definicija v Git, engine in lastni runner; največ toliko podrobnosti, kolikor jih obiskovalec potrebuje |
| Primeri | Read-only review, manual change in issue to draft PR; stanje preverjanja pri vsakem |
| Pregled pred objavo | Vsebina, preverjanja in odločitev; brez obljube univerzalnega sandboxa ali pravilnosti kode |
| Kratek FAQ | Predpogoji, podprti agenti, stroški ponudnika, podatki in meje lastnega runnerja |
| Zaključek in footer | Ponoven CTA, dokumentacija, repo, Apache-2.0 ter Created by Sašo Sečnjak |

**Get started** vodi na dejanski začetni korak, ne na nedelujoč release installer. Ob njem je jasno navedeno, da trenutno preizkušamo izvorno kodo na `main`. Končni CTA se spremeni glede na dejansko stanje ob dostavi.

Aktualni blok iz README:

```sh
git clone https://github.com/runestra-app/runestra.git
cd runestra
bash scripts/quick-start.sh
```

Besedilo ob bloku pove: macOS ali Linux, Git in Bash, brez Dockerja ali AI računa za prvi shell primer; skripta poskrbi za potrebne odvisnosti, oba procesa in vzorčni run. Ciljna rezultata sta **Sample succeeded** v terminalu in uspešen run v brskalniku. Pred javno uporabo ta tok ponovno preverimo v ustreznem testnem okolju.

Ločeno ponudimo začetni AI primer iz [vodiča](../../docs/getting-started.md#try-an-ai-workflow). Ne prikazujemo shell uspeha kot uspeha AI toka.

Stran ne vsebuje izmišljenih testimonialov, uporabniških številk, značk skladnosti, cenika ali nedostopnih cloud funkcij. Znane meje so kratke in ob relevantni trditvi, ne skrite v velikem opozorilnem odseku.

**Kriterij:** obiskovalec v kratkem pregledu razume produkt, lastno izvajalno okolje in prvi korak; na mobilnem zaslonu ukazi ter CTA ostanejo berljivi.

## 7. Faza E: izdelava in preverjanje

**Dostava:** odzivna lokalna stran na podlagi izbrane postavitve. Ena stran ne potrebuje novega backend sistema. Implementacija je v `website/` kot Vite/React statična stran.

Izvorna koda strani sodi v ločeno mapo `website/` v repozitoriju Runestra. Raziskovalni PNG-ji ostanejo v Runestra-product. Razvojna navodila in CGP sodijo pod `development/`, uporabniški setup ostane pod `docs/`. Izvozi potrjenih javnih brand assetov so lahko skupaj s stranjo. Git repo in način dostave ostaneta pregledljiva.

Merila preverjanja:

1. Prikaz pri 360, 768 in 1440 px, dodatno preverjanje dolgega ukaza in povečanega besedila.
2. Vse navigacijske povezave, CTA, FAQ in Copy command dejansko delujejo. Kopiranje ima vidno povratno informacijo in alternativo ob neuspehu.
3. Primerjava izbrane zasnove in implementacije v istem viewportu; popravek tipografije, odmikov, obrezovanja ter hierarhije.
4. Tipkovnica, fokus, semantična struktura, alt opisi, kontrast in zmanjšano gibanje. Cilj za besedilo je najmanj 4,5 : 1, za veliko besedilo 3 : 1.
5. Screenshoti ustrezajo dejanski različici; nič zasebnega ni v slikah, HTML ali metapodatkih.
6. Setup ukazi ustrezajo uporabniškemu vodiču. Če vključimo YAML, ga preverimo z `aorch validate` in jasno navedemo zahtevano različico konfiguracije.
7. Naslov, description, favicon, Open Graph, canonical in končni URL so usklajeni. Datoteka za deljenje ima ustrezen izvoz, npr. 1200 × 630 px.
8. Optimizirane slike, določene dimenzije in brez nepotrebnega JavaScripta. Performance rezultat in pogoje meritve zabeležimo; številke niso garancija vseh omrežij.
9. Brez napak v konzoli ali nedostopnih assetov; veljaven build, preverjene relativne povezave in `git diff --check`.

Obseg ne vključuje prenove vseh operativnih zaslonov. Prenos logotipa v obstoječo glavo, favicon in dokumentacijo lahko izvedemo kot majhno ločeno integracijo; ob tem preverimo obe temi in ne spreminjamo izvedbe workflowov.

## 8. Faza F: predaja in javna objava

Pred objavo pripravimo konkreten pregled: končni logotip, priročnik, delujočo lokalno stran, seznam trditev in preizkusov ter natančen cilj gostovanja. Uporabnik potrjuje pregledljiv rezultat.

Za objavo potrebujemo odločitev o gostovanju, potrjen nadzor domene, izbran URL avtorjevega profila in pot do javnega repozitorija. To ne blokira priprave logotipa ali lokalne strani. Novega računa, plačljive storitve ali analitike ne uvajamo samodejno.

Po izrecnem navodilu za objavo izvedemo deploy, uredimo potrebne nastavitve domene in preverimo HTTPS, odziv, vse povezave, preview za deljenje ter mobilni prikaz na objavljeni strani. Shranimo objavljeno verzijo in način vrnitve na prejšnjo.

**Kriterij:** obiskovalec z javnega naslova pride do dejansko uporabnega začetnega koraka. Če produktni sprejem še ni zaključen, je stran jasno predstavitev in preizkus predizdaje. Objave strani ne pogojujemo z dodajanjem novih funkcij produktu.

## 9. Zaporedje odločitev in odvisnosti

| Točka | Kdo odloči | Kaj je pred odločitvijo pripravljeno |
|---|---|---|
| Vizualna osnova | Uporabnik, že potrjeno | Temni Signalni tok |
| Logo koncept | Uporabnik ob priporočilu izvajalca | Trije primerljivi koncepti, majhne velikosti in konteksti |
| Finalni logotip | Uporabnik | Dodelava izbranega znaka in izvozi |
| Postavitev strani | Uporabnik ob priporočilu izvajalca | Tri postavitve v isti smeri, desktop in mobilni prikaz |
| Predaja | Uporabnik | CGP, lokalno preverjena stran in konkretne odprte omejitve |
| Javna objava | Uporabnik | Natančen deploy cilj in pregledljiva končna vsebina |

Kritična pot je logo izbor, finalna identiteta, izbor postavitve, implementacija in QA. Besedilo ter zajem javno varnih dokazov lahko potekata med logo dodelavo. AI pomaga pri konceptih, pisanju, izdelavi, izvozih in ponovljivih preverbah; človeška izbira, optični pregled in ocena resničnosti ostanejo potrebni.

## 10. Okvir napora

Ocena predpostavlja AI podprto delo, ponovno uporabo obstoječih materialov, eno stran, največ dva kroga dodelave logotipa in en večji popravek postavitve. To je razpon za planiranje, ne ponudba ali rok.

| Sklop | Aktivno delo z AI in pregledom izvajalca |
|---|---|
| Brief, besedilo in izbor dokazov | 2 do 3 ure |
| Logo koncepti, dodelava in vektorski paket | 4 do 8 ur |
| CGP in predloge uporabe | 3 do 5 ur |
| Zasnova strani in izbor | 2 do 4 ure |
| Izdelava odzivne strani | 4 do 7 ur |
| QA, predaja in preprosta objava | 3 do 5 ur |
| **Skupaj** | **18 do 32 ur** |

Dodatno računamo približno 2 do 4 ure tvojega časa za izbire in vsebinski pregled. Ob hitrem odzivu je okvir približno 3 do 5 delovnih dni. Čakanje na DNS, dostop do gostovanja, zunanje uporabnike ali zahtevnejšo vektorsko dodelavo ni vključeno. Če izbrani logo potrebuje specializiranega oblikovalca, njegov napor ocenimo po konceptu.

Oceno zožimo po izboru logotipa in postavitve. Največja negotovost je kakovost ter finalizacija znaka, ne osnovna izdelava statične strani. Širša prenova appa in odpravljanje produktnih sprejemnih napak sta ločen obseg.

## 11. Zaključna kontrola

- [x] Izbran in dodelan vektorski znak; pri 16 px uporabimo favicon, samostojni znak priporočamo pri 24 px ali več.
- [x] Vektorski master, mint/svetla/temna različica, lockup, PNG izvozi in favicon.
- [x] Priročnik z barvami, tipografijo, razmiki in pravili uporabe.
- [x] Angleško besedilo z aktualno Apache-2.0 licenco in preverljivimi trditvami.
- [x] Resnični, lokalni prikaz produkta brez poverilnic ali zasebnih podatkov.
- [x] Izbrana postavitev 2 je implementirana za desktop in mobilno uporabo.
- [x] Delujoč setup blok, CTA, dokumentacija in GitHub povezave.
- [x] Opravljene lokalne vizualne, funkcijske in odzivne preverbe.
- [x] Social preview je pripravljen v 1200 × 630 px, z vektorskim virom in PNG izvozom.
- [x] Lokalna predaja je dokumentirana. Gostovanje, domena in javna objava čakajo na ločeno odločitev.

## 12. Stanje po izdelavi, 12. oktober 2026

- Digitalni brand kit, vodnik uporabe, vektorski logotipi, PNG izvozi, favicon in Open Graph slika so pripravljeni v [visual-identity/](../../../Runestra-product/visual-identity/README.md). Znak je v engine glavi in faviconu; svetla in temna tema uporabljata ustrezni različici.
- Uporabnik je izbral drugo landing postavitev. Odzivna lokalna stran je v [website/](../../website/).
- Prikaz produkta uporablja posnetek dejanske lokalne aplikacije z uspešnim `commands` runom, ne generirane študije.
- Lokalna stran prestane `npm run build`, `npm run test:sites`, mobilni prelom pri 360 in 390 px, tablični pri 768 px ter desktop pri 853 in 1440 px. Copy quick start, FAQ, mobilna navigacija, povezava CTA, asseti in konzola so preverjeni z lokalnim Chromom.
- Brskalniška primerjava z izbrano maketo je shranjena v `website/verification/design-comparison.png`. Izbrana študija je velika 854 × 1840 px, primerjalni izrez implementacije ima enako velikost, celostranski desktop zajem pa 854 × 2760 px. Mobilni celostranski zajem je 390 × 3250 px.
- Resnični posnetek engine v temi Signalni tok je osvežen na 1280 × 700 px; prikazuje uspešen lokalni `commands` run in novi znak v glavi.
- Podroben seznam odstopanj, testov in zaključek je v [website/design-qa.md](../../website/design-qa.md).
- Domena, canonical URL, gostovanje in javna objava ostanejo ločene od lokalne predaje.

Zahtevana lokalna CGP in odzivna pristajalna stran sta pripravljeni. Pred javno uporabo ostajajo registracija ali preverba znamke, potrditev domene in gostovanja, nastavitve končnega URL-ja ter ponovni pregled objavljene strani.
