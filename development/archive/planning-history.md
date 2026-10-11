# Historical planning record

Consolidated on 2026-10-12. This archive preserves the original design decisions,
contracts and dated verification reports from six earlier plans. Statements such
as “current”, “not started”, protocol numbers and test counts describe the date
of the original document. They are not current status.

The [architecture notes](../architecture.md) describe current component
boundaries. Current user guides in `docs/` describe supported behavior. Do not
use the proposed examples in this archive as setup instructions.


Original document: `competitiveness-plan.md`.

## Plan za večjo konkurenčnost

Datum: 2026-10-11. Status: plan potrjen v obsegu, implementacija še ni začeta.

Za prvo javno izdajo je pripravljen ožji [plan implementacije javnega MVP](planning-history.md), ki čaka na izrecno potrditev. Spodnji dokument ostane širši načrt konkurenčnosti; njegov celoten obseg ni pogoj za MVP in ne predstavlja dovoljenja za začetek nove implementacije.

Izhodišče je primerjava s konkurenco v
PHASE-03-konkurenca.md (separate product research workspace). Najbližji
konkurenti so Archon, cezar, Multica in GitHub Agentic Workflows. Runestra je
pred njimi pri plasti zaupanja: agent predlaga, workflow preveri, objavi pa se
samo preverjena vsebina, enkrat in ponovljivo. Zaostaja pri vidnih
funkcijah: sprožilcih, obvestilih, integracijah, stroških, odobritvah in
številu agentov. Ta plan zapre te vrzeli in pri tem ohrani njeno prednost.

### Potrjene odločitve

1. Izvedemo vseh deset točk iz razdelka "Hitri dobički" in "Srednji obseg":
   - webhook sprožilec;
   - cron;
   - prikaz stroškov in tokenov;
   - obvestila;
   - GitHub Issues;
   - licenca;
   - odobritve v vmesniku;
   - Linear;
   - več agentov;
   - GitLab MR.
2. Integracije s sistemi, kot so ticket trackerji, morajo biti **modularne**.
   Uporabnik mora lahko sam dodati integracijo kot korak workflowa:
   avtentikacija na API, nato API klici. Engine in runner hkrati prinašata
   nabor pripravljenih modulov, ki jih uporabnik samo uporabi.
3. Nove integracije gradimo kot module, ne kot nove trdo kodirane handlerje.
   Obstoječi Jira modul prenesemo v isti format in s tem preverimo zasnovo.
4. Objava na PyPI počaka, dokler projekt ni v celoti javen, ker se vmesniki še
   hitro spreminjajo.
5. Izbira licence ostaja odločitev lastnika; predlog iz
   PHASE-01 (separate product research workspace) je MIT.

### Izhodišče iz trenutne kode

| Kaj že obstaja | Kje | Pomen za plan |
|---|---|---|
| Ročni zagon z vhodi in zapisom vrste sprožilca | `Store.enqueue`, tabela `occurrences` (`manual`, `periodic`) v [storage.py](../../src/aorch/engine/storage.py) | Webhook je nova vrsta sprožilca nad istim zagonom |
| Periodični urnik | `next_due` se izračuna v `register`, `tick` in `defer_project` | Cron zamenja izračun naslednjega termina na teh treh mestih |
| Generičen discovery | korak vrne `outputs.work_items`; `discovery.source` je poljuben niz | Vsak modul, ki vrne delovne postavke, je lahko vir |
| Projektne akcije | `actions` z `argv`, `files`, `input_schema`, `result_schema` | Uporabnik že lahko doda korak s skripto |
| Register vgrajenih akcij | `ACTION_REGISTRY` v [schemas.py](../../src/aorch/extensions/schemas.py), dispatch po predponi imena v [extensions/__init__.py](../../src/aorch/extensions/__init__.py) | Register je trdo kodiran; za module ga je treba odpreti |
| Povezave na runnerju | `connections` v `runner.yaml` (`url`, `email`, `token_env`) | Osnova za splošne povezave |
| Zaščita poverilnic | `environment()` v [executor.py](../../src/aorch/runner/executor.py) blokira vse `token_env` in API ključe | Pravilno, a uporabniška skripta danes ne more varno dobiti tokena |
| Evidenca zunanjih učinkov | [ledger.py](../../src/aorch/extensions/ledger.py), oznaka `external_effect` v registru | Moduli z učinki jo morajo podedovati |
| Dogodki agentov | Claude `stream-json`, Codex `--json` v [agents.py](../../src/aorch/runner/agents.py), OpenAI API šteje tokene | Vir za prikaz stroškov |

### Zasnova modulov

#### Pojmi

| Pojem | Pomen |
|---|---|
| **Modul** | Verzioniran paket akcij za en zunanji sistem, npr. `jira`, `linear`, `slack`. Ima manifest, sheme vhodov in izhodov ter implementacijo akcij. |
| **Akcija modula** | En korak workflowa, npr. `linear.discover`. Implementacija je deklarativni HTTP klic ali skripta. |
| **Povezava** | Poimenovana konfiguracija na runnerju: modul, URL, način avtentikacije in ime okoljske spremenljivke s skrivnostjo. Projekt pozna samo ime povezave. |
| **Vmesnik** | Standardna pogodba za vrsto sistema, npr. `tracker` ali `review`. Modul ga lahko implementira, workflow pa ostane neodvisen od konkretnega sistema. |

#### Viri modulov

1. **Vgrajeni moduli** so del runnerja: Jira, GitHub, GitLab, Linear in kasneje
   drugi. Runner jih oglaša kot danes oglaša pogodbe vgrajenih akcij.
2. **Projektni moduli** so v `.aorch/modules/<ime>/module.yaml` in se zapakirajo
   v nespremenljiv posnetek s hashi, enako kot prompti in skripte. Run je tako
   ponovljiv tudi z uporabniškim modulom.
3. **Deljeni moduli** iz Git repozitorija, pripeti na commit, za ponovno rabo med
   projekti. To je kasnejša razširitev in ni v prvi dostavi.

Imena vgrajenih modulov (`jira`, `github`, `gitlab`, `linear`, `git`, `core`) so
rezervirana. Podvojeno ime modula ali akcije je napaka z navedbo obeh datotek,
enako kot pri uvozih verzije 3.

#### Povezava na runnerju

Skrivnosti ostanejo na runnerju, projekt pa jih nikoli ne vidi. Obstoječi format
Jira povezav ostane veljaven.

```yaml
## runner.yaml
connections:
  tracker-main:
    module: linear
    url: https://api.linear.app
    auth:
      type: bearer          # bearer, basic ali header
      token_env: LINEAR_TOKEN
    projects: [PROJECT_UUID]  # neobvezna omejitev na projekte
```

Pravila:

- HTTPS je obvezen, razen za izrecno dovoljen lokalni razvoj.
- Akcija sme klicati samo gostitelja svoje povezave. To prepreči, da bi modul
  token poslal na drug naslov ali dostopal do notranjih naslovov.
- Token dobi samo proces ali HTTP klic akcije, ki uporablja to povezavo.
  Shell koraki, agenti in druge akcije ga ne dobijo; `environment()` ostane
  privzeto zaprt.
- Vrednost tokena se redigira v logih in rezultatih.
- Povezava je vezana na organizacijo runnerja in neobvezno na projekte.

#### Deklarativna HTTP akcija

Najpreprostejša pot za uporabnika je modul brez kode. Akcija opiše klic,
runner pa doda avtentikacijo, preveri vhode, izvede klic in preslika odgovor.

```yaml
## .aorch/modules/youtrack/module.yaml
name: youtrack
version: 1
implements: [tracker]
connection:
  auth: [bearer]
actions:
  discover:
    input_schema:
      type: object
      properties:
        query: {type: string, minLength: 1}
      required: [query]
      additionalProperties: false
    http:
      method: GET
      path: /api/issues
      query:
        query: {input: query}
        fields: {value: "idReadable,summary,description"}
    outputs:
      work_items:
        from: response
        map:
          id: idReadable
          title: summary
          description: description
  comment:
    effect: true
    idempotency_key: [issue, marker]
    input_schema:
      type: object
      properties:
        issue: {type: string}
        text: {type: string}
        marker: {type: string}
      required: [issue, text, marker]
      additionalProperties: false
    http:
      method: POST
      path: /api/issues/{issue}/comments
      path_params:
        issue: {input: issue}
      body:
        text: {input: text}
```

Uporaba v workflowu je en korak:

```yaml
steps:
  find:
    type: action
    action: youtrack.discover
    inputs:
      connection: tracker-main
      query: "project: APP #Unresolved"
```

Ostane načelo brez implicitne interpolacije nizov:

- vrednosti pridejo samo iz preverjenih vhodov (`input`) ali konstant (`value`);
- parametri poti so izrecno našteti in URL-kodirani;
- preslikava odgovora podpira samo pikčaste poti in sezname;
- omejeni so čas klica, velikost odgovora in število strani.

Paginacija (cursor ali povezava na naslednjo stran) je del druge iteracije
modulov.

#### Akcija s skripto

Za zahtevnejšo logiko ostane obstoječi model projektnih akcij. Skripta dobi
JSON na stdin in vrne JSON, preverjen s shemo. Novo je, da akcija deklarira
`connection`. Runner skripti preda:

- neskrivne podatke povezave (URL, uporabnik) v stdin;
- skrivnost v eni okoljski spremenljivki, npr. `RUNESTRA_CONNECTION_TOKEN`,
  samo za ta proces.

```yaml
actions:
  sync:
    run:
      argv: [python3, '@module/sync.py']
    files: [sync.py]
    connection: required
    input_schema: {...}
    result_schema: {...}
```

#### Standardni vmesniki

Vmesnik določi imena akcij ter vhodne in izhodne sheme. Workflow, ki uporablja
vmesnik, zamenja sistem samo z drugo povezavo, brez spremembe korakov.

| Vmesnik | Akcije | Ključna pogodba |
|---|---|---|
| `tracker` | `discover`, `get`, `comment`, `transition` | Delovna postavka ima obvezen `id` ter neobvezne `title`, `url`, `description`, `labels`, `status` in `updated`. To je razširitev obstoječega `work_items`. |
| `review` | `open`, `status`, `comment` | Odpre ali uskladi PR oziroma MR in vrne URL, številko, vejo in preverjen head commit, enako kot `github.open_pr` |
| `notify` | `send` | Pošlje sporočilo kanalu in vrne identifikator sporočila |

Engine preveri vhode in izhode po pogodbi vmesnika, čeprav ne pozna
konkretnega modula na runnerju.

#### Učinki in plast zaupanja

Akcija z `effect: true` mora deklarirati `idempotency_key`. Runner zapiše
namero v evidenco učinkov pred klicem in izvedbo po klicu. Pri negotovem izidu
gre run v `reconciling`, enako kot danes pri Jira komentarju ali PR. Uporabniški
moduli s tem podedujejo glavno prednost Runestre brez dodatnega dela.
Akcija brez oznake učinka ne sme uporabljati metod `POST`, `PUT`, `PATCH` ali
`DELETE`.

#### Orodja za avtorje modulov

- `aorch modules list` in `aorch modules describe IME` prikažeta vgrajene in
  projektne module, vmesnike ter sheme.
- `aorch modules new IME` ustvari predlogo modula z manifestom in primerom.
- `aorch modules test IME.AKCIJA --connection ... --inputs datoteka.json` lokalno
  izvede akcijo proti pravemu API-ju in pokaže preslikan rezultat.
  Za akcije z učinki zahteva izrecno potrditev.
- `aorch validate` preveri manifeste, sheme in reference na vmesnike.

### Faze implementacije

Vrstni red sledi učinku in odvisnostim. Fazi 1 in 2 sta hitra dobička. Faza 3
mora biti pred fazo 4, da nove integracije ne nastanejo kot trdo kodirani
handlerji.

#### Faza 0: Licenca

Lastnik izbere licenco. Dodamo datoteko `LICENSE` in obvestila o avtorstvu.
PyPI objava ostane odložena (potrjena odločitev 4).

Kriterij zaključka: repozitorij ima licenco, ki ustreza načrtu javne objave.

#### Faza 1: Webhook sprožilec in cron

Workflow v verziji 2 in 3 dobi novi vrsti sprožilca:

```yaml
triggers:
  - type: cron
    schedule: "0 7 * * 1-5"
    timezone: Europe/Ljubljana
  - type: webhook
    secret_env: RUNESTRA_WEBHOOK_REVIEW   # na engineu, ne v projektu
    inputs_schema: {...}                   # neobvezno
```

Spremembe:

- Webhook endpoint preveri HMAC podpis telesa s skrivnostjo iz zasebne
  konfiguracije enginea. Ni dovolj, da je skrivnost del URL-ja.
- Telo zahtevka se preveri z `inputs_schema` in preda zagonu kot `inputs`.
  Vrsta sprožilca je zapisana kot `webhook`.
- Omejimo velikost telesa in pogostost zahtevkov.
- Podpisi ponudnikov: GitHub (`X-Hub-Signature-256`) in generični HMAC. Druge
  ponudnike dodamo kot preslikave podpisov.
- Cron izračuna naslednji termin na treh obstoječih mestih. Ohrani se obstoječe
  združevanje zamujenih terminov in preprečevanje prekrivanja runov. Dovoljen
  je en sprožilec vsake vrste na workflow; omejitev "največ en sprožilec"
  postane "največ en sprožilec na vrsto".

Kriterij zaključka: podpisan zahtevek ustvari run z vhodi. Napačen podpis,
prevelik ali neveljaven zahtevek je zavrnjen brez razkritja obstoja workflowa.
Cron po restartu enginea ne ustvari zamujenih runov za nazaj.

#### Faza 2: Osnova modulov

Dostava:

- Manifest modula, nalaganje projektnih modulov in pakiranje v posnetek.
- Splošne povezave na runnerju z avtentikacijo `bearer`, `basic` in `header`.
- Deklarativni HTTP izvajalec in skriptne akcije s povezavo.
- Register akcij postane sestavljen iz vgrajenih in projektnih modulov.
  Dispatch po predponi imena nadomesti register handlerjev.
- Pogodbe vmesnikov `tracker`, `review` in `notify`.
- Evidenca učinkov za akcije z `effect: true`.
- CLI `aorch modules`.
- Jira se prestavi v format vgrajenega modula. Imeni `jira.discover` in
  `jira.comment` ter obstoječi format povezav ostaneta veljavna.

Ker se spremeni pogodba med enginom in runnerjem, zvišamo verzijo protokola.
Engine nadgradimo pred runnerji, nezdružljiv par pa dobi jasno napako.

Kriterij zaključka:

- Uporabniški modul brez kode izvede discovery in komentar proti testnemu API-ju.
- Token ni viden v logih, rezultatih, posnetku ali drugih korakih.
- Klic na drug gostitelj je zavrnjen.
- Prekinjen komentar gre v `reconciling` in se ne podvoji.
- Obstoječi Jira workflow deluje brez sprememb.

#### Faza 3: Vgrajeni moduli

Vsi kot moduli faze 2:

| Modul | Vmesnik | Akcije | Opomba |
|---|---|---|---|
| `github` | `tracker`, `review` | `discover`, `comment`, `open_pr` | Issues prek REST API. `github.open_pr` ostane alias obstoječe akcije. |
| `linear` | `tracker` | `discover`, `comment`, `transition` | GraphQL API |
| `gitlab` | `tracker`, `review` | `discover`, `comment`, `open_mr` | Issues in MR, tudi self-hosted GitLab |
| `slack`, `teams` | `notify` | `send` | Za sporočila iz workflowa, npr. objavo poročila |

Kriterij zaključka: isti coding workflow teče z Jira, GitHub Issues in Linear
samo z zamenjavo povezave. Za vsak modul obstaja preizkus proti testnemu
računu ali posnetemu odgovoru API-ja.

#### Faza 4: Obvestila o stanju runov

Obvestila o zaključku, napaki ali `needs_input` pošilja **engine**, ker pozna
stanje runa tudi takrat, ko runner ni dosegljiv. Uporabljajo isti vmesnik
`notify`, a z zasebno konfiguracijo kanalov na engineu, vezano na organizacijo:

```yaml
notifications:
  on: [failed, needs_input, needs_attention]
  channel: ops-slack     # ime kanala iz zasebne konfiguracije enginea
```

Prva dostava: Slack in Teams incoming webhook, generični webhook in e-pošta
prek obstoječe Brevo povezave. Sporočilo vsebuje projekt, workflow, stanje,
povzetek in povezavo na run, nikoli pa logov ali skrivnosti.

Kriterij zaključka: obvestilo pride enkrat na spremembo stanja. Napaka
pošiljanja ne vpliva na run in je vidna v sistemskih napakah.

#### Faza 5: Stroški in tokeni

- Runner iz dogodkov agentov zbere porabo tokenov, model in strošek: Claude
  končni `result` dogodek, Codex dogodki porabe, OpenAI API števec.
- Poraba se shrani k poskusu in se sešteje na run in workflow.
- Vmesnik jo prikaže na koraku, runu in pregledu workflowa.
- Kjer ponudnik stroška ne poroča, se izračuna iz cen v profilu ali prikaže
  samo poraba tokenov.
- Kasneje: proračun na workflow in dan, ki ustavi nove rune.

Kriterij zaključka: poraba je vidna za vse tri obstoječe adapterje. Manjkajoči
podatki so jasno označeni, ne prikazani kot nič.

#### Faza 6: Odobritve in odgovori v vmesniku

Nov tip koraka:

```yaml
approve:
  type: approval
  needs: [review]
  summary_ref: steps.review.outputs.summary
  approvers: [owner, admin]
  timeout_seconds: 86400
```

- Korak med čakanjem ne zaseda runnerja ali leasea.
- Odločitev zapiše uporabnika, čas in opombo; izhod `decision` je uporaben v
  `when`.
- Ob izteku se korak zaključi z vnaprej določenim izidom, privzeto zavrnitvijo.
- `needs_input` dobi v vmesniku obrazec za odgovore. Odgovor ustvari nov run s
  svežim posnetkom in vhodom `answers`, brez Jire.

Kriterij zaključka: odobritev in zavrnitev delujeta za vlogo z dovoljenjem.
Član brez dovoljenja dobi napako. Odobritev po spremembi preverjene vsebine se
ne prenese na novo vsebino.

#### Faza 7: Več agentov

- Splošen adapter za CLI agente opiše ukaz, način podajanja prompta, omejitev
  orodij in branje izhoda.
- Pri agentih brez lastnega vsiljevanja sheme runner izlušči končni JSON in ga
  preveri s shemo. Neveljaven izhod je napaka koraka, ne tihi uspeh.
- Prvi vgrajeni adapterji: Gemini CLI, OpenCode, Copilot CLI.
- Runner zazna nameščene CLI-je in jih oglaša kot zmožnosti.
- Preučimo podporo za ACP, ki bi pokrila več agentov z enim adapterjem.

Kriterij zaključka: isti preverljiv workflow uspe z vsakim novim adapterjem.
Omejitev orodij ima pri vsakem dokumentiran učinek.

### Vpliv na primerjavo s konkurenco

| Vrzel iz primerjave | Faza | Stanje po planu |
|---|---|---|
| Webhooki, GitHub eventi, cron | 1 | Pariteta z Archonom in cezarjem; gh-aw ostane močnejši pri GitHub eventih |
| Integracije (GitHub Issues, Linear, GitLab) | 2, 3 | Pariteta s cezarjem. Modularnost brez kode je prednost pred Archonom in Multico. |
| Obvestila | 4 | Pariteta |
| Stroški | 5 | Pariteta s cezarjem; gh-aw ostane močnejši pri omejitvah |
| Odobritve | 6 | Pariteta z Archonom in Multico, povezana s preverjeno objavo |
| Število agentov | 7 | Manjša vrzel; Multica ostane močnejša |
| Licenca | 0 | Odpravljena |

Plast zaupanja se okrepi. Moduli podedujejo evidenco učinkov in usklajevanje,
odobritev pa se veže na preverjeno vsebino.

### Izven obsega

- Objava na PyPI, dokler projekt ni v celoti javen.
- Sandbox in omejitev omrežja za agente.
- Chat bot za Slack, Telegram ali Discord kot vir ukazov.
- Urejevalnik workflowov, variante runov, namizne in mobilne aplikacije.
- Deljeni moduli iz zunanjih repozitorijev in javni katalog modulov (kasnejša
  razširitev faze 2).

### Preverjanje

- Pogodbeni testi za manifeste, vmesnike, podpise webhookov in cron izraze.
- Varnostni testi za zavrnjen tuj gostitelj, redakcijo tokena in zavrnjen
  podpis.
- Integracijski testi engine in runner za webhook, modul z učinkom,
  `reconciling` po prekinitvi in odobritev.
- Ročni preizkus v brskalniku za stroške, odobritve, odgovore na `needs_input`
  in obvestila.
- Zunanje integracije označimo kot preverjene šele po preizkusu s pravim
  testnim računom. Simulirani odgovori tega ne dokazujejo.


Original document: `implementation-plan.md`.

## Runestra, plan implementacije

Engine upravlja YAML workflowe, razporeja korake na avtenticirane runnerje in omogoča spremljanje prek browserja. Runner izvaja akcije in agente v lokalnem okolju. Prva uporaba je obdelava Jira ticketov do PR, jedro pa podpira tudi workflowe brez Gita. Engine in runner morata delovati lokalno kot dva procesa ter na ločenih računalnikih prek istega protokola.

Stanje na 2026-10-09: lokalna implementacija zajema vseh deset faz. To pomeni kodo, primere in lokalne teste, ne pa uspešnega zunanjega sprejemnega testa za vsak ponudnik in mobilno platformo.

| Faza | Lokalno stanje | Meja preverjanja |
|---|---|---|
| 1. Konfiguracija in pogodbe | Implementirano in testirano | Primeri konfiguracije validirani |
| 2. Trajno stanje in scheduler | Implementirano in testirano | SQLite, discovery, omejitve in restart testi |
| 3. Runner | Implementirano in integracijsko testirano | Engine in runner sta lokalno izvedla workflow ukazov prek HTTP |
| 4. Browser pregled | Implementirano in ročno preverjeno | Uspeh, izvajanje, napaka in offline stanje so zajeti v `docs/evidence/` |
| 5. CLI agenti | Codex in Claude adapterja implementirana | Lokalni CLI-ja sta uspešno izvedla enak preverljiv primer |
| 6. Jira | Akcije in idempotentna obravnava implementirani | Mock transport testi; Jira povezave in dovoljeni ticketi niso bili podani |
| 7. Git workflow | Worktree, preverjanje, commit in push implementirani | Lokalni bare remote je testiran; GitHub PR ni preverjen |
| 8. Mobilne naprave | iOS Simulator in Android emulator akcije implementirane | Testni aplikaciji nista bili podani, dejanski app test ni bil izveden |
| 9. API agent | OpenAI Responses runtime in orodja implementirani | Mock API test; `OPENAI_API_KEY` ni bil na voljo za live klic |
| 10. Operacije in prekinitve | Recovery, tokeni, backup, pruning in navodila implementirani | Lokalni recovery in backup testi; oddaljena namestitev ni bila izvedena |

Konfiguracijska sintaksa in postopek uporabe sta v `docs/configuration.md` in `docs/operations.md`.

Zadnje preverjanje po prenovi browserja: 57 testov uspešnih, Ruff in JavaScript sintaktični pregled brez napak, wheel paket vsebuje browser sredstva. V Playwrightu so preverjeni prijava, projektni pregled, pregled čakajočega izvajanja, graf odvisnosti, tema in mobilna postavitev. Pytest izpiše eno opozorilo o prihodnji spremembi povezave med Starlette TestClient in httpx.

### Dogovorjen obseg

- Projektni direktorij `.aorch/` vsebuje konfiguracijo, workflowe, profile agentov, skille in prompte.
- Centralni engine ima trajno stanje, urnik, čakalno vrsto in spletni pregled.
- Runner prevzema delo, sporoča kapaciteto ter obnavlja časovno omejene rezervacije nalog.
- Vsak runner uporablja svoj generirani token. Dovoljenja in največjo kapaciteto določa engine.
- Koraki podpirajo izvršljive akcije, shell ukaze in agentne naloge prek CLI ali API runtimea.
- CLI uporablja lokalno prijavo ali eksplicitno API avtentikacijo. Neposredni API runtime uporablja API poverilnice.
- Discovery teče vsakih pet minut in na runnerju pridobi Jira tickete za konfigurirano osebo in status.
- Vsak ticket ima samostojno obdelavo. Začetna omejitev je ena aktivna obdelava ticketa na workflow.
- Ob nejasnostih se poskus zaključi z rezultatom `needs_input`. Runner se sprosti. Ponovna analiza se izvede ob naslednjem periodičnem triggerju.
- Testiranje uporablja rezerviran iOS simulator ali Android emulator na macOS runnerju.
- Commit, push, Jira komentarji in PR so eksplicitne akcije workflowa.

### Tehnološka odločitev

Uporabnik nima preference. Izberemo Python za engine in runner, ker večina začetnega dela zajema upravljanje procesov, datotek, Git ukazov in zunanjih API klicev. Skilli lahko uporabljajo poljubne jezike.

| Del | Izbira |
|---|---|
| Runtime | Python 3.12 ali novejši, paket s `pyproject.toml` in zaklenjenimi odvisnostmi |
| Engine API | FastAPI in Uvicorn |
| Konfiguracija | YAML z varnim loaderjem, Pydantic modeli in JSON Schema za rezultate |
| CLI | Typer, ukazi pod `aorch` |
| Stanje | SQLite na lokalnem disku enginea, transakcije in migracije |
| Čakalna vrsta | Tabele v isti SQLite bazi, brez dodatnega brokerja v prvi različici |
| Runner | `asyncio`, asinhroni HTTP odjemalec in nadzor podprocesov |
| Browser pregled | HTML, CSS in JavaScript kot ločena paketirana sredstva, REST in SSE |
| Artefakti | Datoteke na engineu, metapodatki v bazi; lokalni workspace ostane na runnerju |
| Preverjanje | pytest, integracijski testi in dejanski lokalni scenariji |

Prva namestitev ima en engine proces z enim schedulerjem. Ne zaženemo več Uvicorn workerjev nad istim schedulerjem. Več engine instanc, leader election, PostgreSQL in porazdeljena hramba so kasnejša razširitev. SQLite ne bo na skupnem omrežnem disku.

### Struktura projekta

```text
src/aorch/
  cli.py
  config/
  contracts/
  engine/
    api/
    scheduler/
    storage/
    web/
  runner/
    executors/
    agents/
    workspaces/
    resources/
  extensions/
tests/
  unit/
  integration/
examples/
  local-actions/.aorch/
  jira-coding/.aorch/
  document-analysis/.aorch/
docs/
```

`contracts` vsebuje skupne podatkovne modele in verzijo protokola. Engine ne uvaža lokalnega izvajalnega okolja runnerja. Provider logika je v adapterjih oziroma razširitvah, ne v schedulerju.

### Konfiguracija in njena distribucija

Ločimo tri ravni:

1. `.aorch/config.yaml` in spremljajoče datoteke določajo projektno avtomatizacijo.
2. `engine.yaml` določa bazo, hrambo, bind naslov in avtentikacijo browserja. Projekti se upravljajo v spletnem vmesniku.
3. `runner.yaml` določa identiteto, URL enginea, token datoteko, lokalne adapterje, delovne mape in naprave.

Engine lahko uporablja `.aorch/` trenutnega direktorija pri lokalnem razvoju. V običajni uporabi se Git projekti dodajo v spletnem vmesniku; njihovi workflowi so v repozitoriju. Reference na datoteke so relativne glede na projektno konfiguracijo in omejene na dovoljene korene.

Ob nalaganju preverimo verzijo, neznane ključe, reference, cikle, pogoje, konfiguracijo akcij in zahtevane adapterje. Brez samodejnega nameščanja skritih odvisnosti ob branju konfiguracije.

Vsak Run dobi nespremenljiv posnetek konfiguracije, promptov in uporabljenih skillov s hashi. Runner ga prejme kot preverjen paket ob prevzemu dela; ne potrebuje dostopa do datotečnega sistema enginea. Izvršljive odvisnosti morajo biti nameščene na runnerju in prijavljene med njegovimi zmogljivostmi. Izvorna koda ciljnega projekta se pridobi ločeno prek workspace akcije.

Veljavne spremembe veljajo za nove zagone. Aktivni zagoni uporabljajo svoj posnetek. Neveljavna sprememba ohrani zadnjo veljavno konfiguracijo in pokaže napako. Definicije v bazi niso skrivnosti; konfiguracija vsebuje samo reference na poverilnice.

### Domenski model in življenjski cikel

| Objekt | Namen |
|---|---|
| Project / WorkflowRevision | Projekt in verzionirana definicija workflowa |
| TriggerOccurrence | Konkreten periodični ali ročni dogodek |
| WorkItem | Stabilna identiteta dela iz določenega vira |
| WorkItemSnapshot | Opis, komentarji, priloge in provider podatki za konkreten poskus |
| Run | Izvajanje discovery ali obdelave posameznega Work Itema |
| StepRun / Attempt | Izvajanje koraka in njegov posamezen poskus |
| Runner / Lease | Odobren izvajalec in rezervacija konkretne naloge |
| Workspace / ResourceLock | Delovno okolje in izključna uporaba workspacea ali naprave |
| Event / Artifact | Dogodki, dnevniki in prenosljivi rezultati |
| ExternalEffect | Evidenca Jira komentarja, pusha ali ustvarjenega PR |

Run uporablja stanja `queued`, `running`, `succeeded`, `needs_input`, `failed`, `cancelled`, `needs_attention` in `reconciling`. `needs_input` je zaključeno izvajanje, ne aktiven proces. Work Item ohrani podatke o nejasnostih in preteklih poskusih. Naslednji trigger ustvari nov poskus s svežim snapshotom; ne nadaljuje starega CLI procesa.

Rezultat koraka je strukturiran: `status`, `summary`, `outputs`, `artifacts`, `workspace_revision` in morebitna napaka. `workspace_revision` je neobvezen, ker workflow ni nujno Git workflow. Analiza dodatno vrne `decision: ready | needs_input` in seznam nejasnosti z identitetami. Pogoji uporabljajo omejen izrazni jezik, brez Python `eval`.

Vzporednost določa `needs` in razpoložljiva kapaciteta. Popravljalne zanke modeliramo kot omejen blok ponavljanja, ne kot cikle v grafu odvisnosti. Ponovni pregled in testi po spremembi invalidirajo prejšnje rezultate preverjanja.

### Pogodba med engineom in runnerjem

Runner vzpostavi izhodno HTTP povezavo in prevzema delo z omejenim long pollingom. Engine ostane avtoriteta za dodelitve, dovoljenja in stanje.

Predvidene operacije:

- registracija runnerja in pogajanje o verziji protokola;
- heartbeat z aktivnimi poskusi, kapaciteto in lokalnimi viri;
- atomaren prevzem naloge z `lease_id`, generacijo in rokom veljavnosti;
- obnovitev lease, oddaja dogodkov in nalaganje artefaktov;
- oddaja strukturiranega rezultata in potrditev preklica;
- ponovna povezava ter uskladitev aktivnih procesov in že oddanih rezultatov.

Vsak zahtevek ima identiteto runnerja. Rezultat mora pripadati njegovi trenutni dodelitvi. Ponovna oddaja istega rezultata je idempotentna; rezultat stare generacije ne sme premakniti trenutnega stanja. Zaporedne številke dogodkov omogočajo ponovno pošiljanje po prekinitvi.

Lease ustvari engine. Runner ob izgubi lease prekine procesno skupino in ustavi nove zunanje akcije. Iztek lease sam po sebi ni dokaz, da podproces ni več aktiven. Engine zato negotov poskus preklopi v `reconciling` in ga ne dodeli drugemu runnerju brez uskladitve. Pri nepreverljivem izidu zahteva uporabniško odločitev.

### Avtentikacija in lokalne poverilnice

Engine generira ločen naključen token za vsak odobren runner in shrani samo njegov hash. Runner shrani token v datoteko z omejenimi dovoljenji. Engine določi dovoljene projekte, oznake in največjo kapaciteto; samoprijavljene zmogljivosti ne širijo dovoljenj.

Token velja za registracijo in vse nadaljnje runner zahteve. Podprta sta preklic in rotacija. Runner lahko pridobi samo svoje naloge ter odda svoje dogodke, rezultate in artefakte. Ne dobi administratorskih pravic. Browser ima ločeno prijavo in sejo.

Lokalni razvoj privzeto posluša na loopback naslovu. Oddaljena namestitev uporablja HTTPS prek konfiguriranega reverse proxyja. Engine token se ne posreduje agentnim podprocesom. Lokalna CLI prijava in provider API ključi ostanejo na runnerju oziroma pri izrecno konfiguriranem izvajalcu.

### Faze implementacije

#### Faza 1 Konfiguracija in pogodbe

Implementiramo paket, CLI, YAML loader, validacijo, podatkovne modele, generiranje shem, posnetke konfiguracije in verzijo runner protokola. Zamrznemo prvo sintakso `needs`, pogojev, vhodov, rezultatov in omejenega ponavljanja.

Kriterij zaključka: `aorch validate` preveri minimalen coding in document workflow. Napake pokažejo datoteko in polje. Primeri vsebujejo veljavne reference; cikli, neznani profili in neveljavni pogoji so zavrnjeni.

#### Faza 2 Trajno stanje in scheduler

Implementiramo migracije, transakcijsko čakalno vrsto, state machine, urnik, pripravljanje korakov, deduplikacijo, omejitve vzporednosti in dogodke. Periodični triggerji ne ustvarjajo vzporednih discovery zagonov istega vira. Po restartu zamujene intervale združimo v en aktualen pregled, brez množičnega nadomeščanja starih dogodkov.

Aktiven Work Item ima največ en aktiven Ticket Run znotraj konfiguriranega obsega. Odsotnost ticketa v enem discovery rezultatu sama po sebi ne pomeni, da ga prekličemo; preverjanje upravičenosti aktivnega dela je ločena eksplicitna akcija.

Kriterij zaključka: dva hkratna prevzema ne dobita iste naloge; nič rezultatov uspešno zaključi discovery; dva Work Itema ustvarita dve obdelavi, omejitev ena pa ju izvede zaporedno. Restart ohrani vrsto in zaključene rezultate.

#### Faza 3 Runner in izvršljivi koraki

Implementiramo registracijo, tokene, long polling, heartbeat, lease, reconnect, nadzor podprocesov, timeout, preklic in prenos dogodkov ter artefaktov. Akcije se izvajajo prek eksplicitnih argumentov in stdin. Shell način je samostojen, izrecno deklariran tip koraka.

Runner ima trajen lokalni journal, da po restartu prepozna prejšnje poskuse. Omejimo velikost dnevnikov in prenosov. Rezultati morajo prestati deklarirano shemo. Nespremenljivi paketi ne smejo omogočiti pisanja zunaj dovoljenih map pri razpakiranju.

Kriterij zaključka: engine in runner lokalno izvedeta workflow dveh resničnih ukazov. Neveljaven oziroma preklican token je zavrnjen. Timeout prekine celotno procesno skupino. Ponovna povezava ne ustvari dvojnega zaključka koraka.

#### Faza 4 Browser pregled

Implementiramo seznam workflowov, urnikov, Work Itemov, Runov in runnerjev. Podrobnosti izvajanja pokažejo korake, pogoje, poskuse, dnevnike, rezultate, rezervirane vire in artefakte. SSE prenaša nove dogodke; REST omogoča popolno obnovitev pogleda po ponovni povezavi.

Prva različica omogoča spremljanje, ročni zagon in preklic. Zahtevki, ki spreminjajo stanje, imajo preverjanje prijave in zaščito seje. Retry za negotove zunanje učinke ni slep gumb za ponovitev.

Kriterij zaključka: uporabnik v browserju vidi aktivno nalogo, dokončanje, napako in offline runner. Po ponovnem odpiranju strani je prikaz skladen z bazo. Runner token ne omogoča dostopa do administratorskih operacij.

#### Faza 5 Skilli in CLI agenti

Implementiramo katalog lokalnih skillov, nalaganje `SKILL.md`, referenc in promptov ter eksplicitne deklaracije izvršljivih akcij. Standardni skill brez dodatnega manifesta je uporaben kot agentno navodilo. Akcija, ki jo kliče engine, potrebuje deklarirano vstopno točko in vhodno ter izhodno shemo.

Prvi agent adapter je Codex CLI, drugi Claude CLI. Adapter pripravi skill kontekst, upravlja sejo in zajema strukturirane dogodke. Profil loči izvajalca, avtentikacijo, model, orodja in omejitve. Lokalni način ne prevzame API ključev iz nepovezanega okolja. Nepodprta zmogljivost povzroči jasno napako pred izvajanjem.

Kriterij zaključka: oba CLI adapterja opravita isti majhen workflow z istim skillom in preverljivim rezultatom. Lokalna prijava deluje pod uporabnikom runnerja. Napaka avtentikacije je vidna v browserju. Ukaz `/analyze` ni univerzalna pogodba; workflow referencira skill, adapter pa določi način njegovega klica.

#### Faza 6 Jira discovery in obravnava nejasnosti

Implementiramo prvi source paket z akcijama za pridobivanje Jira ticketov in objavo komentarjev. Oseba, status, projekt, query in connection so konfiguracija. Provider podatki se preslikajo v Work Item in snapshot, jedro ostane neodvisno od Jire.

Analiza pregleda tudi komentarje in ohranjene nejasnosti. Objavljanje komentarjev beleži identiteto nejasnosti ter provider ID komentarja. Pred ponovnim pisanjem preverimo že izveden učinek. Kjer provider nima idempotentne operacije, uporabimo stabilen marker in preverjanje; pri nejasnem izidu ne obljubljamo izvajanja natanko enkrat.

Kriterij zaključka: dejanski Jira test vrne nič in dva primerna ticketa. Nejasen ticket enkrat dobi vprašanja in sprosti runner. Naslednji petminutni pregled ne podvoji komentarjev. Odgovor se upošteva pri naslednji analizi in omogoči nadaljevanje. Objavljen PR prepreči novo implementacijo po pravilih workflowa.

#### Faza 7 Git workspace in coding workflow

Implementiramo pripravo repozitorija, izoliran worktree in branch za ticket, beleženje začetnega commita, runner affinity in izključno uporabo workspacea. Analiza, plan in implementacija uporabljajo isti workspace. Workflowi brez Gita uporabljajo navadno mapo.

Implementiramo pot `analyze`, `solve gaps` ali `plan`, `implement`, `review`, test in zaključne Git akcije. Povratna zanka implementacije ima nastavljivo omejitev. Commit, push in odprtje PR so ločene akcije s preverjanjem obstoječega rezultata. Ponovni poskus ne prepiše tujih sprememb in ne ustvari novega PR za isto obdelavo.

Kriterij zaključka: dva ticketa imata ločena workspacea. Review napaka sproži popravilo, izčrpana omejitev pa `needs_attention`. Na testnem repozitoriju dejanska izvedba ustvari commit, push in en PR. Končno preverjanje potrdi, da je PR commit enak preverjeni različici kode.

#### Faza 8 iOS simulator in Android emulator

Implementiramo katalog naprav, rezervacije, preflight preverjanje, zagonske in zaključne akcije ter shranjevanje poročil in posnetkov. Testni ukazi, naprava in pričakovani rezultati so konfiguracija projekta; engine ne vsebuje produktno specifične testne logike.

Kriterij zaključka: na macOS runnerju uspe dejanski test na eni iOS in eni Android napravi. Dve nalogi ne uporabljata iste naprave hkrati. Neuspešen test blokira objavo PR. Preklic sprosti napravo šele po potrjeni ustavitvi uporabe.

#### Faza 9 API agent runtime

Implementiramo API adapter in agentno zanko z omejenimi orodji za branje, pisanje in ukaze v workspaceu. API ključ ostane pri izvajalcu, ki kliče API. Vključimo skill kontekst, strukturiran zaključek, timeout ter omejitev iteracij in porabe.

Najprej podprimo enega dejanskega ponudnika, OpenAI, prek izoliranega adapterja. Drugi ponudniki uporabljajo isto pogodbo. Avtentikacija ni vezana na izbiro workflowa ali formata skillov.

Kriterij zaključka: isti majhen workflow uspe prek CLI lokalne prijave, CLI API avtentikacije in neposrednega API runtimea. Neposredni API runtime dejansko uporabi orodje v workspaceu; odgovor modela brez agentne zanke ne šteje kot ta funkcionalnost.

#### Faza 10 Prekinitve in namestitev

Zaključimo uskladitev po restartu, rotacijo tokenov, varnostne kopije baze in artefaktov, omejitve hrambe, redakcijo konfiguriranih skrivnosti ter dokumentacijo za lokalno in oddaljeno namestitev. Runner se zažene kot uporabniški proces ali storitev z istim dostopom do CLI prijave in mobilnih orodij kot uporabnik.

Kriterij zaključka: preverjeni scenariji izgube povezave med korakom, restart enginea, restart runnerja in prekinitev neposredno po Jira komentarju ali PR. Sistem obnovi preverljive rezultate in označi negotove izide. Lokalni engine in oddaljeni macOS runner izvedeta isti primer kot lokalna dva procesa.

### Vrstni red prvih dostav

1. Faze 1 do 4: delujoč engine, avtenticiran runner, trajna vrsta in browser pregled za izvršljive akcije.
2. Faze 5 do 7: dejanski agenti in Jira coding workflow od ticketa do PR.
3. Faza 8: preverjanje na obeh mobilnih platformah.
4. Fazi 9 in 10: neposredni API runtime ter zaključen preverjen operativni paket.

Preverjanje prekinitev se razvija že od faze 2; faza 10 ga dopolni s celotnimi dejanskimi scenariji. API pogodba je določena v prvi fazi, njena izvajalna implementacija pa pride po delujočih CLI adapterjih.

### Preverjanje celotnega sistema

- Pogodbeni testi konfiguracije in rezultatov, brez podvajanja implementacije.
- Integracijski testi atomarnega prevzema, lease, deduplikacije, reconnect in invalidacije starih rezultatov.
- Testne podprocesne priprave za prekinitve in deterministične napake. Te niso nadomestilo za resnične adapterje.
- Dejanski testi Codex in Claude avtentikacije brez prikaza poverilnic.
- Dejanski Jira testni projekt in Git testni repozitorij za preverjanje komentarjev ter PR.
- Test na iOS simulatorju in Android emulatorju z razpoložljivim testnim projektom.
- Preprost workflow nad dokumentom, ki nima Git workspacea ali Jira vira.
- Browser preverjanje poti od ročnega oziroma periodičnega zagona do prikaza rezultata.

Za preostale sprejemne teste potrebujemo Jira testno povezavo in dovoljene tickete, oddaljeni Git/GitHub testni repo z dovoljenji za PR, mobilna testna projekta ter API ključ, če želimo ponoviti live API klic. Lokalni Codex in Claude CLI testa sta bila izvedena. Brez manjkajočih zunanjih sistemov ne morem trditi, da so te zunanje poti preverjene.

### Meje prve različice

Prva različica podpira več runnerjev pod enim engineom, ne več aktivnih engine instanc. Workspace je vezan na runner, ki ga je pripravil. Prenos aktivnega coding workspacea med runnerji, samodejno nameščanje odvisnosti, javna tržnica skillov, večuporabniški SaaS in vizualni urejevalnik workflowov so poznejše razširitve. Naprednejša izolacija se dodaja kot izvajalni adapter; osnovna namestitev izvaja dovoljene projektne pakete pod konfiguriranim uporabnikom.

### Referenčni viri

- [Symphony specifikacija](https://github.com/openai/symphony/blob/main/SPEC.md), referenca za izolirano obdelavo dela in ločitev koordinacije od agentov.
- [Agent Skills format](https://agentskills.io/specification), format navodil in spremljajočih skript.
- [FastAPI asinhrono izvajanje](https://fastapi.tiangolo.com/async/), podlaga za asinhroni API.
- [Python asinhroni podprocesi](https://docs.python.org/3/library/asyncio-subprocess.html), podlaga za izvajanje in nadzor CLI procesov.
- [Primerna uporaba SQLite](https://www.sqlite.org/whentouse.html), omejitve izbire za en centralni engine.
- [Codex neinteraktivni način](https://learn.chatgpt.com/docs/non-interactive-mode), izvedba prek CLI in ponovna uporaba lokalne avtentikacije.
- [Claude programatično izvajanje](https://code.claude.com/docs/en/headless) in [avtentikacija](https://code.claude.com/docs/en/authentication), podlaga za drugi CLI adapter.


Original document: `modular-config-and-builtin-actions-plan.md`.

## Plan modularne konfiguracije in vgrajenih akcij

Datum: 2026-10-09. Status: lokalna implementacija in preverjanje sta zaključena.

### Stanje implementacije

Vseh pet lokalnih faz je izvedenih. YAML loader podpira varne gnezdene uvoze,
združevanje in diagnostiko izvora. Snapshot shrani preverjene izvorne fajle.
CLI prikazuje konfiguracijo, sheme in skupni katalog akcij. Engine in runner
se pogajata o različicah pogodb ter preverjata izhode. Git checkout in worktree
imata ekspliciten način in lastnika, celotna lokalna Git pot ter simulirana
obnovitev PR sta pokriti z integracijskim testom. OrcTest uporablja razdeljeno
konfiguracijo in vgrajene akcije.

Zunanji sprejemni test v odvisnem projektu je bil izveden po uporabnikovi
odobritvi. Rezultat je bil poslan v pregled v gostujoči Git storitvi. Lokalni
engine in runner sta ostala pri protokolu 1, zato je bil sprejemni run izveden
z enkratnim klicem runner executorja nad trenutnim modularnim paketom, brez
restarta storitev.

### Cilj in potrjeni odločitvi

Projektni `.aorch/config.yaml` postane kratka vstopna datoteka. Akcije, profili in workflowi se lahko razdelijo v poljubno število lokalnih YAML datotek. Projekt lahko še naprej uporablja eno samo datoteko.

Uporabnik je potrdil:

1. Uvozi pokrivajo akcije, profile in workflowe.
2. Podvojeno ime pomeni napako z navedbo obeh datotek. Vrstni red uvozov ne določa, katera definicija zmaga.

Engine in runner dobita skupen katalog vgrajenih akcij. Engine preverja pogodbe in združljivost; runner izvaja Git ukaze in ponudnikove operacije z lokalnimi poverilnicami. Projektu za commit, push in GitHub PR ni treba pisati lastnih skript.

### Izhodiščno stanje in dostavljene spremembe

Ugotovitve temeljijo na trenutni lokalni kodi, ne na opravljenem novem zunanjem testu.

| Področje | Izhodiščno stanje | Dostavljeno |
|---|---|---|
| Nalaganje konfiguracije | Neposredno validiranje ene YAML datoteke | `src/aorch/config/loader.py` razreši gnezdene uvoze, cikle, omejitve in izvorne napake |
| Konfiguracijski model | `ProjectConfig` sprejema verziji 1 in 2 | V3 se validira in normalizira v V2; V1 in V2 ohranita enodatotečno obnašanje |
| Posnetek | Normalizirana konfiguracija in referencirane datoteke | Izvorni YAML fajli so hashirani in vključeni v nespremenljivi paket |
| Vgrajene akcije | Akcije so bile opisane ločeno od dispatcha | Skupni katalog vsebuje sheme, verzije, opise, zmogljivosti, predpogoje, učinke in handlerje |
| Git workspace | Checkout in worktree sta uporabljala različni poti priprave | Način `checkout` ali `worktree` in identiteta workspacea sta zabeležena; executor ohrani način za run |
| Commit | Zahteva `verified.json` in uporablja `git add --all` | Omejene poti zavrnejo pobeg in nepovezane staged podatke; fingerprint pokriva Git indeks |
| GitHub PR | `git.pr` uporablja `gh` in ledger učinka | Alias ohrani ključ učinka; preveri branch, base, stanje in commit ter vrne URL, številko in head |
| OrcTest | Dve projektni akciji in objavna skripta | Modularni workflow uporablja vgrajene Git akcije; stare datoteke ostanejo do zunanje potrditve migracije |

Obstoječi Git test preverja worktree, commit, push na lokalni bare remote in spremembo vsebine po preverjanju. Ne dokazuje delovanja GitHub PR ali celotne Git poti skozi običajno dodelitev enginea. Akcij zato ne gradimo ponovno, ampak popravimo njihovo povezovanje in dopolnimo pogodbe.

### Predlagana konfiguracijska sintaksa

Nova izvorna sintaksa uporablja `version: 3`. To loči podporo uvozom od obstoječih verzij in omogoči jasno napako na starejšem engineu.

```yaml
## .aorch/config.yaml
version: 3
name: OrcTest
imports:
  - profiles/writers.yaml
  - profiles/reviewers.yaml
  - actions/notes.yaml
  - actions/checks.yaml
  - workflows/create-note.yaml
  - workflows/review.yaml
```

Vsak fragment vsebuje eksplicitne sekcije. Ime datoteke ne določa njene vsebine. Fragment lahko združi več sekcij za posamezno funkcionalnost.

```yaml
## .aorch/profiles/writers.yaml
profiles:
  writer:
    adapter: claude
    authentication: local
    sandbox: workspace-write
    tools: [read, write, command]
```

```yaml
## .aorch/workflows/review.yaml
workflows:
  review:
    steps:
      inspect:
        type: agent
        profile: reviewer
        prompt: prompts/review.md
```

Glavna datoteka lahko poleg `imports` vsebuje tudi lokalne `profiles`, `actions` in `workflows`. Fragment lahko vsebuje `imports`, `profiles`, `actions` in `workflows`; projektna `name` in `version` sta dovoljena samo v glavni datoteki.

#### Pravila uvozov in združevanja

1. `imports` je seznam eksplicitnih relativnih poti do YAML datotek. Prva dostava ne podpira URL, glob vzorcev, imenikov, okoljskih spremenljivk ali YAML oznake `!include`.
2. Pot uvoza je relativna glede na datoteko, ki ga deklarira. Razrešena pot mora ostati pod korenom glavne konfiguracije, pri običajnem projektu pod `.aorch/`. Absolutne poti in symlinki zunaj korena so zavrnjeni.
3. Poti do promptov, skillov, `actions.files` in `@package/` ostanejo relativne glede na glavni konfiguracijski koren. Premik definicije v fragment ne spremeni njihovega pomena.
4. Podprti so gnezdeni uvozi. Aktivni sklad zazna cikel in izpiše celotno verigo. Že obdelana kanonična datoteka se v istem grafu obdela samo enkrat, tudi če jo uvozita dva fragmenta.
5. Združevanje je unija imenovanih definicij v sekcijah `profiles`, `actions` in `workflows`. Definicije so celote. Ni globokega združevanja posameznih polj, dedovanja ali razdelitve korakov istega workflowa med datoteke.
6. Dve različni datoteki, ali fragment in glavna datoteka, ne smeta definirati istega imena znotraj iste sekcije. Napaka velja tudi za identični definiciji. Isto ime v različnih sekcijah je dovoljeno.
7. Reference se preverijo po združitvi vseh datotek. Workflow zato lahko uporablja profil ali akcijo iz kateregakoli fragmenta, ne glede na vrstni red uvozov.
8. Loader beleži datoteko, vrstico, stolpec in konfiguracijsko pot za posamezno definicijo in polje. Velja tudi za podvojene YAML ključe, napačne sheme, neznane profile in manjkajoče datoteke.
9. Branje celotnega grafa je omejeno, začetno največ 128 različnih datotek, globina 16 in skupno 2 MiB YAML vsebine. Napaka navede preseženo omejitev. Te omejitve ne vključujejo ločeno paketiranih promptov in skript.

Primer diagnostike:

```text
Duplicate profile 'writer':
  profiles/writers.yaml:3:3
  profiles/legacy.yaml:6:3
Import cycle: config.yaml -> workflows/shared.yaml -> config.yaml
workflows/review.yaml:8:18: unknown profile 'reviewer'
```

### Nalaganje, posnetki in združljivost

Predlagani tok:

```text
config.yaml
  -> safe YAML loading with source locations
  -> import graph resolution
  -> merge named definitions
  -> source-aware validation
  -> normalized executable configuration
  -> immutable snapshot
```

Izvorni model verzije 3 je ločen od izvršilnega `ProjectConfig`. Uvozi se razrešijo samo pri nalaganju projekta na engineu oziroma v CLI. Runner prejme že združeno konfiguracijo in ne bere uvozov iz svojega trenutnega checkouta.

Ker uvozi ne spreminjajo semantike korakov, se izvorna verzija 3 prevede v obstoječo izvršilno verzijo 2. Izvorna verzija je ohranjena v metapodatkih posnetka. Verziji 1 in 2 ohranita dosedanje obnašanje in ne sprejemata `imports`. `aorch schema` objavi tako izvorno kot izvršilno shemo.

Posnetek vsebuje vse obiskane YAML datoteke, že uporabljene prompte, skille in skripte ter determinističen zemljevid izvora. Vsebina se prebere enkrat in isti zajeti bajti služijo razčlenjevanju in hashiranju. Zemljevid uporablja relativne poti, ne absolutnih poti računalnika. Metapodatki ne smejo kolidirati s podatki, ki jih paket trenutno že uporablja za `repository`.

Glavni hash zajema tudi izvorne YAML datoteke in metapodatke. Sprememba komentarja zato lahko ustvari novo revizijo, čeprav je izvršilna konfiguracija enaka. To je namerna sledljivost, ne obljuba enakega hasha za enodatotečno in modularno postavitev. Hash ostane ponovljiv za iste zajete datoteke.

Aktivni run ostane na svojem posnetku. Sprememba samo uvoženega fragmenta mora pri refreshu ustvariti novo revizijo za nove zagone. Neveljaven fragment prepreči registracijo nove revizije; zadnja veljavna revizija in njen urnik ostaneta ohranjena, napaka pa je vidna v projektu.

Prva faza uvozov lahko ohrani paketni protokol 1, ker runner že sprejema dodatne metapodatke paketa in validira normalizirano konfiguracijo. Razširjena pogajanja o pogodbah akcij zahtevajo posebej verzionirano spremembo protokola, opisano spodaj. Podpora modularni konfiguraciji sama po sebi ne dokazuje podpore novejšim akcijam na starejšem runnerju.

CLI dobi `aorch config show PROJECT`, ki pokaže združeno konfiguracijo, ter možnost `--sources` za izvor definicij. `aorch validate` in `aorch snapshot` uporabljata isti loader. Brez avtomatskega prepisovanja projektnih datotek.

### Skupen katalog vgrajenih akcij

Sedanja `ACTIONS` in `SCHEMAS` zamenjamo z enim registrom opisov akcij. Vsak opis vsebuje ime, morebitne aliase, verzijo pogodbe, vhodno in izhodno shemo, potrebne zmogljivosti, predpogoje workspacea, kratek opis ter oznako zunanjega učinka.

Definicije in sheme ne uvažajo izvršilne kode runnerja. Izvedbeni dispatch se na runnerju naloži ločeno in preveri, da ima vsak deklarirani izvajalec ustrezen opis. Validacija, CLI katalog, dokumentacija in scheduler uporabljajo isti register.

Predlagana prva dostava:

| Akcija | Namen in sprememba |
|---|---|
| `git.prepare` | Ohrani pripravo worktreea iz eksplicitnega lokalnega repozitorija in njegovega aliasa v `runner.repositories` |
| `git.branch` | Nova akcija pripravi branch nad checkoutom pripetega projektnega commita, preden agent spreminja datoteke |
| `git.verify` | Ohrani izvajanje preverjevalnih ukazov in zapis preverjene vsebine |
| `git.commit` | Doda opcijski seznam `paths`; za staro pogodbo ohrani commit vsega, nova raba priporoča eksplicitni seznam |
| `git.push` | Ohrani push preverjenega commita in preverjanje oddaljenega refa |
| `github.open_pr` | Jasnejše novo ime za GitHub operacijo; `git.pr` ostane podprto ime iste operacije |

Obstoječe Jira, device, repair in core akcije vključimo v isti katalog brez nepotrebnega spreminjanja njihovih pogodb. Dodatne integracije se dodajajo na podlagi konkretnega workflowa. Prva dostava ne potrebuje sistema zunanjih pluginov ali generične akcije za poljubne Git ukaze.

Katalog je dosegljiv z `aorch actions list` in `aorch actions describe git.commit`, tudi v JSON obliki. Opis navede vhodne in izhodne podatke, Git/gh odvisnosti, lokalno avtentikacijo in predpogoje. Katalog ne pomeni, da so orodja na runnerju nameščena ali prijavljena.

Pri izvorni konfiguraciji verzije 3 projektne akcije ne smejo prevzeti rezerviranih imenskih prostorov kataloga, na primer `git.*` in `github.*`. Za verziji 1 in 2 se dosedanja prednost projektne definicije ohrani z opozorilom ob konfliktu, da ta sprememba ne pokvari obstoječih projektov brez migracije.

#### Pogodbe in zmogljivosti runnerja

Danes se imena vgrajenih akcij preverjajo prek `adapters`. Ta mehanizem ohranimo za stare pogodbe. Za nove pogodbe dodamo eksplicitno oglaševanje kanoničnih imen in verzij akcij ter preverjenih lokalnih orodij. Samoprijavljene zmogljivosti ne spreminjajo dovoljenj runnerja.

Nov paket pripne zahtevano verzijo pogodbe uporabljene akcije, ne njene celotne Python implementacije. Nov runner podpira tudi stare pogodbe. Star paket brez verzije uporablja obstoječo pogodbo. Engine dodeli nov korak samo runnerju, ki podpira zahtevano pogodbo; drugače run ostane v vrsti z jasnim razlogom.

Razširitev registracije je protokol 2 z izrecno podporo staremu protokolu 1 na novem engineu. Star runner prejema samo stare pogodbe in paketno obliko, ki jo razume. Nadgradnja po vrstnem redu engine, nato runnerji, nato projekti. Star engine novega runnerja brez podpore protokolu 2 jasno zavrne. To preverimo pogodbeno, ne predpostavimo na podlagi skupne številke paketa.

### Poenotenje Git poti

`prepare_repository` in akcije uporabljajo skupen model workspacea: identiteta repozitorija, način `checkout` ali `worktree`, začetni commit, branch in lastništvo workspacea. Obstoječi workspace z nezdružljivimi metapodatki zahteva recovery; podatkov ne prepišemo tiho.

`git.branch` nad običajnim projektom uporabi checkout, ki ga je runner že pripravil iz posnetka. Ustvari stabilen `codex/...` branch iz identitete runa in zapiše metapodatke. Ne potrebuje vnosa projekta v `runner.repositories`. Izvede se pred agentom in zahteva čist začetni checkout. Ne uporablja samodejnega force checkouta nad spremenjenimi datotekami. Ponovni klic iste akcije z istimi podatki obdrži obstoječi branch.

`git.prepare` ostane pot za workflowe, ki potrebujejo drug repozitorij. Executor preskoči pripravo projektnega checkouta za ta korak in prepozna zabeležen `worktree` pri naslednjih korakih. Poti se ne kolidirata in runner ne ponastavi worktreea. Integracijski test zajame `executor.execute` za oba načina.

Commit, push in PR še naprej zahtevajo uspešno preverjanje iste vsebine. Za preprost OrcTest zadostujejo `git diff --check` in preverjanje pogodbe ustvarjene datoteke; to ne pomeni testiranja aplikacije. Akcije naj pri manjkajoči pripravi ali preverjanju vrnejo razumljivo napako s predpogojem.

`git.commit.paths` je seznam relativnih poti znotraj repozitorija, nikoli shell izraz. Zavrne pobeg iz korena, zunanje symlinke in nepovezane že staged datoteke. Preverjanje mora zajeti tudi indeks, tako da drugačna staged vsebina ne obide preverjene vsebine. Za dinamični OrcTest agent vrne tudi `outputs.paths: [filename]`; `input_refs` prenese seznam brez novega interpolacijskega jezika.

Verzija 2 Git pogodb vrne uporabne strukturirane podatke: commit in branch, pri pushu tudi remote, pri PR pa URL, številko, base in head commit. Stare pogodbe in ime `git.pr` ohranimo. Rezultate preverimo na runnerju in ob sprejemu na engineu, ne samo vhode.

#### PR in ponovitve

`github.open_pr` in `git.pr` uporabljata isti kanonični izvajalec, identiteto učinka in obstoječe lastniške markerje. Alias ne sme povzročiti drugega PR ali prekiniti evidence starega runa.

Po pushu preverimo oddaljeni commit. Pri iskanju in ponovni uporabi PR preverimo repozitorij, head branch, base branch, odprto stanje, lastništvo in head commit. Zaprt ali mergean PR ni avtomatsko uspešen odprt PR. Pri nejasnem izidu zunanjega klica sledi uskladitev oziroma `needs_attention`, ne slepo ustvarjanje novega PR. Ohranimo argumentne sezname in `--body-file`.

Shranjevanje v engineu trenutno vsebuje preverjanje objavljenega dela prek prefiksa `git.pr:`. Pri uvajanju aliasa uporabimo eksplicitni `kind` učinka; stare zapise še vedno prepoznamo. Dodamo regresijski test, da discovery po objavljenem PR ne zažene istega dela ponovno. Ne obljubljamo izvajanja zunanjih klicev natanko enkrat.

### Migracija OrcTest

Predvidena končna struktura:

```text
.aorch/
  config.yaml
  profiles/
    writers.yaml
  workflows/
    create-note.yaml
  prompts/
    create_test_note.md
```

OrcTest po zamenjavi generičnih objavnih skript ne potrebuje sekcije `actions`. Ločene projektne akcije vseeno demonstrira samostojen primer v `examples/modular-config`, z vsaj dvema datotekama za vsako podprto sekcijo.

Workflow ohrani javno ime `analyze` in postane:

```text
prepare_branch -> create_note -> verify_note -> commit_note -> push_branch -> open_pull_request
```

`create_note` ohrani obstoječi prompt in vrne `filename`, `paths`, `commit_message` ter `pr_description`. `verify_note` preveri ime, obstoj, vsebino in omejitev sprememb na eno ustvarjeno datoteko, nato zapiše Git preverjanje. `commit_note` uporabi `git.commit`, `push_branch` uporabi `git.push`, zadnji korak uporabi `github.open_pr`. Za naslov PR lahko uporabi že vrnjen `commit_message`, zato dodatna interpolacija ni potrebna.

`repository`, `base`, `remote` in povezovanje rezultatov so eksplicitni podatki korakov. Ne ugibamo cilja PR iz poljubnega lokalnega remotea. Stare skripte odstranimo šele po preverjeni migraciji in pregledu morebitnih drugih referenc.

Migracija ne dodaja urnika. Zunanji sprejemni test je bil enkratni ročni zagon, izveden po izrecni odobritvi uporabnika.

### Faze in kriteriji zaključka

| Faza | Dostava | Kriterij zaključka |
|---|---|---|
| 1. Modularni loader | Zaključeno | Vsi trije tipi definicij se uvozijo; konflikti in cikli imajo regresijske teste |
| 2. Posnetek in orodja | Zaključeno | Sprememba fragmenta spremeni hash; CLI in primer sta validirana |
| 3. Katalog in pogodbe | Zaključeno | En katalog napaja CLI, validacijo, scheduler in dispatch; nezdružljiv runner je blokiran, neveljaven rezultat zavrnjen |
| 4. Uporabne Git akcije | Zaključeno lokalno | Executor preveri checkout in worktree, omejen commit, lokalni push ter simulirano obnovitev PR po izgubljenem odgovoru |
| 5. Primeri in migracija | Zaključeno | Dokumentacija, OrcTest konfiguracija in en zunanji sprejemni run so preverjeni; PR #3 ostaja odprt |

Fazi 1 in 2 sta prva uporabna dostava. Faze 3 do 5 so druga dostava. Modularizacija ne čaka na razširitev Git akcij.

#### Dostavljena mesta sprememb

| Datoteke | Spremembe |
|---|---|
| `src/aorch/config/__init__.py`, `loader.py`, `yaml.py` | Ločen loader, parser brez podvojenih ključev, graf uvozov in izvorna diagnostika |
| `src/aorch/config/models.py` | Obstoječi izvršilni model ostane cilj normalizacije V3 |
| `src/aorch/extensions/schemas.py`, `__init__.py` | Skupen katalog pogodb in dispatch glede na katalog |
| `src/aorch/extensions/git.py`, `src/aorch/runner/executor.py` | Skupno stanje checkouta in worktreea, Git pogodbe in PR operacija |
| `src/aorch/runner/executor.py`, `package.py`, `client.py` | Priprava workspacea, validacija pogodb, paket in zmogljivosti |
| `src/aorch/contracts/__init__.py` | Registracija protokola 2 in eksplicitna kompatibilnost |
| `src/aorch/engine/api.py`, `storage.py`, `runtime.py` | Refresh diagnostika, dodeljevanje glede na pogodbe, sprejem rezultatov, zunanja evidenca |
| `src/aorch/cli.py` | Združena konfiguracija, izvor, katalog akcij in sheme |
| `tests/test_configuration.py`, `test_extensions.py`, `test_api_runner.py`, `test_engine.py`, `test_recovery.py` | Pogodbeni, integracijski in recovery testi |
| `docs/configuration.md`, `docs/profiles-and-actions.md`, `README.md`, novi primer | Navodila in preverljivi primeri |
| Odvisni testni projekt | Lokalna modularizacija in migracija objavne poti |

### Preverjanje in omejitve

Izvedeni pregledi:

```text
uv run pytest -q: 73 passed
uv run ruff check src tests: passed
uv run aorch validate dependent test project: passed
uv run aorch validate examples/modular-config: passed
OrcTest live workflow: commit pushed, PR #3 OPEN and MERGEABLE
Pinned snapshot regression: passed against an advanced local branch and OrcTest remote
```

Lokalni testi pokrivajo več datotek za vse tri sekcije, gnezdeni diamantni graf,
konflikte v glavni datoteki in fragmentih, cikle, nevarne poti, symlinke,
omejitve globine, števila datotek in velikosti ter samostojnost paketa po
odstranitvi izvornih YAML fajlov. Pokriti so protokol registracije V1/V2,
blokiranje runnerja brez zahtevane pogodbe, validacija izhoda in katalog CLI.
Git integracija prek `executor.execute` preveri pripeti checkout in zunanji
worktree, omejeno preverjanje ter commit, push na lokalni bare remote in
obnovitev PR po simuliranem izgubljenem odgovoru. Obstoječi testi pokrivajo
idempotenten commit in zavrnitev push-a po spremembi vsebine.

Med pregledom zadnjega neuspelega engine runa je bil odkrit ločen checkout
problem: engine je pripel commit `10c7825`, shallow fetch pa je prinesel samo
novejši `main` commit `6511d01`. Runner zdaj eksplicitno prenese tudi pripeti
commit. Regresijski test premakne vejo naprej pred checkoutom, dodaten pregled
pa je uspešno pripravil isti pripeti commit iz OrcTest remote repozitorija.
Neuspeh tega starega runa zato ni povzročila neobjavljena modularna
konfiguracija; njegov paket je vseboval še konfiguracijo V2. Spremembe v
OrcTest `.aorch/` ostajajo lokalne in jih Git-based engine iz `main` še ne vidi.

Ne pokrivajo vsi predlagani robni primeri avtomatski testi. Dejanski GitHub
transport, zaprt ali tuj PR, neskladen base/head, uskladitev ob prekinitvi po
lokalnem zapisu ter vsak scenarij refresh-a zahtevajo dodatno integracijsko ali
simulacijsko pokritost.

Lokalni Git testi uporabljajo začasne repozitorije, lokalni bare remote in
nadzorovan `gh` transport. Zunanji GitHub test je uporabil prijavljeno lokalno
avtentikacijo in dejanski OrcTest repozitorij.

Izven prvega obsega ostanejo oddaljeni uvozi, dedovanje profilov, prepisovanje definicij, parametrizirani workflow templatei, deljenje korakov istega workflowa med fragmente in sistem zunanjih pluginov. Uvozi rešijo organizacijo definicij. Ponovno uporabo večkorakovnih sklopov obravnavamo posebej, če jo zahtevajo konkretni workflowi.

PR #3 je namenjen pregledu. Združitev ali nadaljnje spremembe na GitHubu niso
bile izvedene.


Original document: `public-mvp-implementation-plan.md`.

## Runestra: plan implementacije javnega MVP

Datum: 2026-10-11.

**Status: lokalna implementacija odobrena. Javni release, push in zunanji sprejemni preizkusi niso izvedeni.**

Produktni cilj in izbor funkcij sta potrjena. Cilj je uporabna javna izdaja za strokovni ugled in pridobivanje zunanjih uporabnikov. Prva ciljna okolja sta macOS in Linux. Podpora za ti okolji še ni zunanje preverjena; Windows in WSL2 nista del sprejemnih kriterijev te izdaje.

Ta dokument določa predlagani obseg javnega MVP in zoži [competitiveness-plan.md](planning-history.md). Širši dokument ostane vir kasnejših možnosti, pregled konkurence (separate product research workspace) pa raziskovalni kontekst, ne dokaz konkurenčne premoči. [implementation-plan.md](planning-history.md) opisuje prvotno implementacijo in zgodovinske rezultate preverjanja.

### 1. Rezultat izdaje

Uporabnik na macOS ali Linuxu namesti engine in runner, izvede primer brez trackerja, poveže Claude Code ali Codex ter nato iz ročne naloge, GitHub issuea ali Jira ticketa pride do preverjene in odobrene objave PR. V vmesniku vidi rezultate, vprašanja, odločitve, napake in razpoložljivo porabo. Ob pomembnih stanjih lahko prejme obvestilo.

Za prvi preizkus niso potrebni Brevo, Jira, oba agenta ali javno dostopen engine. Lokalna prijava ostane izrecno izbrana možnost, omejena na loopback. Običajna prijava in obstoječe funkcije ostanejo podprte.

Osnovni uporabniški tok:

1. Namestitev iz označene izdaje in preverjanje predpogojev.
2. Lokalna prijava, registriran runner in uspešen shell primer.
3. Izbira enega nameščenega in prijavljenega agenta.
4. Pregled repozitorija brez sprememb in objave.
5. Ročna zahteva za spremembo ali discovery iz GitHub Issues oziroma Jire.
6. Implementacija, pregled in testi.
7. Odgovor na vprašanje ali odobritev konkretne preverjene vsebine.
8. Eksplicitni commit, push in osnutek PR; brez samodejnega mergea.

### 2. Obseg in meje

| Vključeno v MVP | Omejitev prve dostave |
|---|---|
| macOS in Linux namestitev | Engine lahko teče v Dockerju, runner na gostitelju z nameščenim CLI |
| Claude Code in Codex | Vsak primer deluje z enim agentom; kombinacija je dodatna možnost |
| Obstoječi OpenAI API adapter | Ohranjena funkcionalnost; ni pogoj za prvi preizkus |
| Ročni zagon z vhodi | Tudi brez trackerja |
| GitHub Issues in Jira Cloud | Polling discovery; skupen nadaljnji coding workflow |
| Odgovori in odobritve v UI | Odločitev je vezana na preverjeno vsebino |
| Interval, cron in webhook | GitHub podpis in generični HMAC; deduplikacija dostav |
| Obvestila | Generični webhook in Slack incoming webhook |
| Tokeni in razpoložljivi stroški | Manjkajoče vrednosti niso nič; ocene so označene |
| Več runnerjev | En coding run ostane na enem runnerju; brez prenosa workspacea med stroji |
| Minimalni vgrajeni moduli | Brez javnega formata poljubnih uporabniških modulov |

Izdaja je namenjena zaupanja vrednim projektom in namenskim runnerjem. Prompt, JSON Schema in worktree niso varnostna izolacija. Lastni runner ne pomeni, da zahtevki za oblačni model ne zapustijo uporabnikove infrastrukture. Dokumentacija opiše dejanske meje vsakega adapterja in ne obljublja splošne skladnosti ali izvajanja nezaupanja vredne kode.

### 3. Izhodišče iz trenutne delovne kopije

- Repo že vsebuje engine, runner, UI, organizacije, lokalno prijavo, intervale, discovery, Git/Jira akcije, journal in evidenco učinkov.
- Namestitvene skripte in Compose datoteke obstajajo. Njihov obstoj ni dokaz uspešne namestitve na čistem okolju.
- `src/aorch/contracts/__init__.py` trenutno določa `PROTOCOL_VERSION = "4"`. Nadaljnja sprememba se načrtuje iz tega stanja, ne iz stare navedbe različice 3.
- `src/aorch/engine/storage.py` uporablja runner affinity. MVP jo ohrani za coding rune.
- Delovna kopija vsebuje obstoječe nepotrjene spremembe. Implementacija jih ne prepisuje, ne resetira in ne obravnava kot že izdane funkcije.
- Stare številke uspešnih testov niso izhodiščni rezultat te izdaje. Faza A pridobi svež rezultat in loči obstoječe napake od novih regresij.

### 4. Zaporedje implementacije

Vsaka faza je ločena pregledljiva sprememba oziroma manjši niz sprememb. Pred začetkom naslednje faze morajo biti opravljeni njeni relevantni kriteriji. Razvojne preveritve in zunanji sprejemni preizkusi imajo ločena stanja.

#### Faza A: stabilizacija, namestitev in diagnostika

**Spremembe**

- Zabeležimo stanje delovne kopije in izvedemo obstoječe teste. Po potrebi okolje pripravimo z `uv sync --locked --all-groups`; teste izvedemo z `uv run --locked --group dev python -m pytest -q`.
- Odpravimo blokade osnovnega izvajanja, organizacijskih dovoljenj, obnove in objave.
- Installer in dokumentacija uporabljata označeno izdajo. Ločimo posodobitev izdaje od razvoja na `main` in ohranimo uporabnikovo konfiguracijo.
- Dodamo `aorch doctor` za engine, protokol, runner, dosegljivost, CLI-je in orodja, potrebna za izbrani primer. Zaznava executablea še ne pomeni uspešne prijave.
- Dodamo izrecni preizkus agenta z opozorilom, da opravi dejanski modelni klic. Diagnostika ne izpisuje poverilnic.
- Runner oglaša dejansko razpoložljive zmožnosti; neusklajena konfiguracija dobi razumljivo napako pred začetkom dela.
- Uredimo lokalno namestitev, ustavitev, ponovni zagon, posodobitev in odstranitev. Odstranitev podatkov je ločeno izrecno dejanje.
- Licenca MIT je dodana v metapodatke paketa in v `LICENSE`, z imetnikom avtorskih pravic Sašo Secnjak.

**Glavna mesta:** `scripts/install-local.sh`, `scripts/install-runner.sh`, Compose datoteke, `src/aorch/cli.py`, `runner/client.py`, `README.md`, `docs/deployment.md`, `docs/operations.md`.

**Kriteriji zaključka**

- Čista macOS in Linux namestitev izvedeta shell primer, lokalna prijava ne zahteva e-poštne storitve.
- Posodobitev ohrani konfiguracijo in podatke; ponovni zagon ne izgubi zgodovine.
- Manjkajoč agent ali nezdružljiv protokol ima jasno navodilo za odpravo.
- Osnovni testi uspejo. Zunanja preverjanja so označena posebej.

#### Faza B: primeri in prvi uporabniški tok

**Spremembe**

- Pripravimo tri vzdrževane primere: pregled repozitorija, ročna zahteva za spremembo in issue do PR.
- Za Claude in Codex omogočimo en sam izbor profila brez ročnega popravljanja vsakega koraka. Primer z mešanima agentoma ostane neobvezen.
- V UI izboljšamo dodajanje projekta, prikaz validacijskih napak, izbiro workflowa in vnos preverjenih vhodov. Ohranimo obstoječi operativni slog.
- Brez skrivnosti pripravimo kopirljive nastavitve povezav. Uporabnik vidi, katera dejanja bodo imela zunanje učinke.
- Prvi primer ne objavlja sprememb. Coding primer privzeto odpre osnutek PR šele po odobritvi, ki jo doda faza C.

**Glavna mesta:** `examples/`, `config/`, `engine/api.py`, `engine/web/`, `README.md`, `docs/configuration.md`.

**Kriteriji zaključka**

- Pregled repozitorija uspe z vsakim agentom posebej, brez trackerja in brez drugega agenta.
- Neveljaven vhod ali manjkajoč predpogoj se pokaže pred izvajanjem.
- Cilj za zunanjega uporabnika s pripravljenimi predpogoji je prvi uspešen primer v 15 minutah; izmerjeni čas zabeležimo, ne predpostavimo.
- Celotna ročna coding pot se preveri po fazi C.

#### Faza C: vprašanja in odobritve brez trackerja

**Spremembe**

- Dodamo strukturirana vprašanja v pogodbo rezultata, ob združljivosti z obstoječim `core.needs_input`.
- UI shrani odgovore in ustvari povezan nov run s svežim posnetkom ter preverjenim vhodom `answers`. Stari run ostane nespremenljiv in v zgodovini.
- Discovery za postavko, ki čaka na odgovor, ne sproža novih enakih runov. Preverjanje postavke, oddaja odgovorov in enqueue potekajo transakcijsko.
- Nov korak `approval` upravlja engine. Čakanje ne zaseda runner capacity ali leasea. Runner affinity in stanje workspacea se ohranita.
- Odobritev vsebuje organizacijo, run, korak, uporabnika, čas, opombo ter identiteto preverjene vsebine in predstavljenih dokazov.
- UI pokaže diff oziroma artefakt in rezultate preverjanj, ne samo agentovega povzetka. Pregledljiv dokaz je na voljo tudi, če runner začasno ni dosegljiv.
- Runner ustvari manifest preverjene vsebine z digestom. Engine shrani predmet odobritve, objavni korak pa zahteva ustrezno odobritev in ponovno preveri digest.
- Sprememba vsebine razveljavi uporabnost odobritve. Zavrnjena ali pretečena odobritev v predlogi onemogoči objavo.
- Privzeti odobritelji so owner in admin v isti organizaciji. Endpointi preverijo dovoljenja, zaščito pred CSRF in podvojeno oziroma sočasno odločitev.
- Določimo stanje čakanja, aktivne omejitve runov, zaklepe virov, preklic in hrambo workspacea. Workspace, potreben za nadaljevanje, se med čakanjem ne izbriše; izgubljen workspace pomeni ponovno preverjanje, ne slepe objave.

**Glavna mesta:** `config/models.py`, `contracts/`, `engine/storage.py`, `engine/scheduler.py`, `engine/api.py`, `engine/web/`, `extensions/git.py`, `runner/`.

**Kriteriji zaključka**

- Odgovor brez Jire ustvari točno en povezan run tudi ob dvojni oddaji obrazca.
- Odobritev, zavrnitev, iztek, preklic in restart enginea delujejo brez izgube odločitve.
- Član brez dovoljenja ali uporabnik druge organizacije ne more odločiti.
- Objava drugačne vsebine od odobrene je zavrnjena.
- Med čakanjem runner lahko izvaja drug run; workspace za odobreno nadaljevanje ostane uporaben.

#### Faza D: minimalni moduli in GitHub Issues

**Spremembe**

- Vgrajene akcije dobijo ekspliciten register handlerjev, pogodbeno različico in preverjene sheme. Izognemo se javnemu novemu jeziku manifestov.
- Povezave ostanejo na runnerju, vezane na organizacijo in dovoljene projekte. Obstoječi Jira format ostane veljaven.
- Definiramo skupno delovno postavko: ponudnik, stabilni ID, URL, naslov, opis in podatki, potrebni za discovery. Identiteta vključuje povezavo, da enaki lokalni ID-ji niso v konfliktu.
- GitHub modul dobi `discover` in `get`, filtre repozitorija ter labels in omejeno paginacijo. UI je primarni kanal za vprašanja; dodatnih komentarjev v GitHub v MVP ne potrebujemo.
- Jira discovery in obstoječi komentar ostaneta podprta. Obe integraciji predajata postavko istemu coding workflowu.
- GitHub PR ostane eksplicitna akcija nad preverjenim in odobrenim commitom. Javna imena obstoječih akcij ostanejo združljiva.
- Pri HTTP klicih določimo timeout, omejitve odgovora, strani in preusmeritev ter ravnanje ob rate limitu. Poverilnice se ne posredujejo agentnim ali navadnim shell korakom.
- Za vsak zapis določimo idempotency ključ in preverjanje zunanjega stanja. Sam lokalni ledger ni dokaz izvedenega učinka. Negotov izid se ne ponavlja samodejno.
- Minimalni registrski vmesnik omogoča kasnejši Linear/GitLab modul, ne obljublja pa zamenjave trackerja samo s spremembo povezave.

**Glavna mesta:** `extensions/__init__.py`, `extensions/schemas.py`, `extensions/jira.py`, nov GitHub modul, runner povezave, discovery v `engine/storage.py`, primeri.

**Kriteriji zaključka**

- GitHub Issues in Jira sprožita isti nadaljnji workflow z ustreznima discovery korakoma.
- Ponovljen discovery in ponovno odprta postavka sledita dokumentirani politiki brez podvojenega aktivnega dela.
- Obstoječi Jira workflow ostane združljiv.
- Paginacija, rate limit, napačna poverilnica in organizacijska omejitev imajo teste.
- Discovery in PR sta preverjena s pravim testnim GitHub računom; Jira s pravim Jira testnim projektom. Mock testi niso zamenjava za ta kriterij.

#### Faza E: sprožilci in obvestila

**Spremembe sprožilcev**

- Ročni zagon in interval ostaneta združljiva. Dodamo cron z IANA časovnim pasom, prikaz naslednjega termina in dokumentirano politiko poletnega časa.
- Pri izpuščeni lokalni uri termin izpustimo; pri ponovljeni lokalni uri izvedemo enkrat. Po daljšem izpadu ni zgodovinskega backfilla: združimo zamujene termine v največ en zagon ob spoštovanju omejitev.
- Naslednji termin in identiteto sprožilca shranjujemo ločeno. Kombinacija različnih sprožilcev ne deli enega nejasnega `next_due`.
- Webhook preveri podpis surovega telesa, velikost, shemo in dovoljen cilj. Skrivnost je v zasebni konfiguraciji enginea.
- GitHub uporablja ID dostave; generični HMAC vključuje ID dogodka in časovni žig v podpisane podatke. Deduplikacija in enqueue sta transakcijska in preživita restart.
- GitHub dostave preverimo po dovoljenem repozitoriju, tipu dogodka in dejanju. Ne sprožamo dela na vsak podpisan dogodek. Payload preslikamo v vhode z izrecno dovoljeno preslikavo, brez izvajanja kode.
- Dodamo rate limit, omejeno hrambo deduplikacijskih zapisov in jasne odgovore brez razkritja skrivnosti. Lokalni MVP ne zahteva javnega webhook endpointa; cron in polling delujeta brez njega.

**Spremembe obvestil**

- Engine ob spremembi stanja v isti transakciji doda zapis v trajni outbox.
- Prva kanala sta generični webhook in Slack incoming webhook. Nastavitve in skrivnosti so zasebne ter vezane na organizacijo.
- Dogodki: napaka, `needs_input`, čakanje na odobritev, `needs_attention`, `reconciling` in uspešen zaključek. Uporabnik izbere, katere želi.
- Sporočilo vsebuje projekt, workflow, stanje in povezavo, brez logov, skrivnosti ali nepreverjenega surovega izhoda agenta.
- Pošiljanje ima omejene ponovitve in vidno napako. Run se zaradi napake obvestila ne spremeni.
- En outbox zapis na spremembo prepreči lokalne dvojnike. Pri timeoutu ponudnika lahko nastane podvojeno sporočilo; kjer ponudnik ne podpira idempotence, ne obljubljamo dostave natanko enkrat.

**Glavna mesta:** `config/models.py`, `engine/storage.py`, `engine/scheduler.py`, `engine/api.py`, nov modul za obvestila, UI in zasebna konfiguracija enginea.

**Kriteriji zaključka**

- Napačen podpis, zastarel generični dogodek, nedovoljen repozitorij in prevelik payload ne ustvarijo runa.
- Podvojena dostava po restartu ustvari največ en run.
- Cron prestane teste menjave ure, restarta in sočasnih sprožilcev.
- Nedosegljiv kanal ne pokvari runa; pomembna stanja imajo preverjeno obvestilo in uporabno povezavo.

#### Faza F: poraba, sprejemni preizkusi in izdaja

**Spremembe**

- Adapterji zbirajo razpoložljive tokene, model in poročan strošek po poskusu. Seštevki vključujejo ponovne poskuse in notranje agentne iteracije brez dvojnega štetja.
- OpenAI API adapter ohranimo; če so podatki na voljo, uporablja isti format porabe.
- Manjkajoče vrednosti so označene kot neznane. Ocenjen API strošek, ponudnikov poročan strošek in dejanska naročniška poraba niso predstavljeni kot ista stvar.
- UI pokaže porabo na koraku in runu. Širši finančni dashboard in proračuni niso del MVP.
- Posodobimo README, konfiguracijo, operacije, varnostne meje in primerjalne trditve. Windows/WSL2 v prvi izdaji ni označen kot preverjeno podprt.
- Objavimo označeno izdajo z opombami, znanimi omejitvami in dokazili o preverjanju. Dokumentacija navede dejanske preverjene OS/CLI različice.

**Kriteriji zaključka**

- Celotna pot od ročne zahteve do osnutka PR uspe s Claudom in Codexom vsakim posebej.
- GitHub in Jira tok, vprašanja in odobritev imajo zunanje sprejemne preizkuse.
- Prekinitev pred zunanjim klicem, po sprejemu klica ter pred lokalnim zapisom rezultata ne vodi v slepo ponovitev objave.
- Spremenjena vsebina po testih ali odobritvi ne pride skozi nadzorovani objavni korak.
- Vsaj trije zunanji uporabniki preizkusijo namestitev in prvi workflow. Vsaj eden na macOS in eden na Linuxu. Zabeležimo čas, blokade in potrebno pomoč.
- Blokade namestitve, izguba podatkov, napačna dovoljenja in nenadzorovana objava se odpravijo pred izdajo.

### 5. Odvisnosti in dostave

| Dostava | Faze | Pregledljiv rezultat |
|---|---|---|
| Lokalna predizdaja | A, osnovni del B | Namestitev, doctor in pregled repozitorija |
| Celoten ročni workflow | C, zaključek B | Vprašanja, odobritev in ročna zahteva do osnutka PR |
| Tracker workflowi | D | GitHub/Jira postavka do istega coding workflowa |
| Avtomatizirana uporaba | E | Cron, webhook in pomembna obvestila |
| Javni MVP | F in vsi sprejemni kriteriji | Označena izdaja z zunanjim preizkusom |

Porabo začnemo tehnično zajemati pri spremembi pogodb v fazi C, UI in končno preverjanje pa zaključimo v F. Obvestila se lahko razvijejo po definiciji stanj v C in niso odvisna od celotne dostave trackerjev.

Pred spremembo protokola pripravimo seznam novih polj in zmožnosti. Različico zvišamo samo ob dejansko nezdružljivi spremembi. Engine in runner imata dokumentiran vrstni red nadgradnje in jasno zavrnitev nezdružljivega para. Ne obljubljamo nadgradnje med aktivnimi runi brez prekinitve.

Vsaka DB migracija ima preizkus nad obstoječim stanjem, backup in navodilo za obnovo. Obstoječe konfiguracije v1 do v3 ostanejo veljavne; novih zmožnosti ne dodajamo z implicitno spremembo vedenja starih workflowov.

### 6. Izven javnega MVP

- Linear, GitLab MR, Gitea, Forgejo in Jira Data Center.
- Gemini CLI, OpenCode, Copilot CLI in ACP; obstoječih adapterjev ne odstranjujemo.
- Javni manifesti uporabniških modulov, deklarativni HTTP jezik, `modules new/test`, deljeni moduli in katalog.
- Neposredno predajanje tokenov poljubnim projektnim skriptam.
- Obljuba, da vsak tracker zamenjamo samo z drugo povezavo.
- Poln sandbox, microVM in omrežni firewall. Zaupanje v projekt in runner ostane izrecna meja izdaje.
- Prenos workspacea in enega coding runa med runnerji.
- Chat ukazi, Teams in e-poštna obvestila o runih. Obstoječa e-poštna prijava ostane.
- Vizualni urejevalnik workflowov, mobilne aplikacije in variante z vzporedno primerjavo diffov.
- Windows in WSL2 kot preverjeni prvi platformi.
- HA, PostgreSQL migracija, billing, upravljana cloud ponudba in celovit finančni dashboard.
- Objava na PyPI; za MVP zadostuje preverjena namestitev označene Git izdaje.

### 7. Preverjanje in dokazi

Uporabimo obstoječi testni sistem. Dodamo smiselne teste pogodb, migracij, dovoljenj, restartov, časovnih robnih primerov, deduplikacije in negotovih zunanjih učinkov. Browser preverjanje pokrije dejanski uporabniški tok, ne samo prikaza komponent.

Za vsako funkcijo zabeležimo ločeno: implementirano, avtomatizirano preverjeno, lokalno integracijsko preverjeno in zunanje sprejemno preverjeno. Rezultate shranimo v `docs/evidence/public-mvp/` z datumom, različicami, scenarijem, rezultatom in znanimi omejitvami; brez skrivnosti.

Javne demonstracije pokažejo:

1. Uspešno ročno nalogo in issue do odobrenega osnutka PR.
2. Zavrnitev objave vsebine, spremenjene po preverjanju ali odobritvi.
3. Prekinitev med zunanjim učinkom in varno razrešitev negotovega izida.

Primerjava s konkurenco ostane hipoteza, dokler ne izvedemo primerljivih praktičnih preizkusov. Za javni MVP niso potrebne trditve o edinstvenosti ali vodstvu na trgu.

### 8. Potrditev, napor in začetek

Potrditev tega dokumenta pomeni dovoljenje za lokalno implementacijo in preverjanje opisanega obsega. Sama po sebi ne dovoljuje objave izdaje, pushanja, zunanjih sporočil ali učinkov v uporabnikovih dejanskih projektih. Zunanje preizkuse izvedemo v izrecno določenih testnih računih in repozitorijih.

Licenco izbere lastnik pred javno izdajo. Testni računi in okolja so odvisnosti sprejemnega preverjanja, ne razlog za ustavitev neodvisnega lokalnega razvoja.

Največji in najbolj negotov del je faza C, ker povezuje trajna stanja, workspace, dokaz preverjanja in dovoljenja. Faza D je srednje velik obseg zaradi zunanjih API-jev, E zaradi časovnih robnih primerov in dostav. Faza A najprej razjasni stanje obstoječih sprememb. Koledarsko oceno pripravimo po izhodiščnih testih in kratki tehnični zasnovi C; trenutno bi bila natančna časovnica ugibanje.

Lokalna implementacija tega plana je bila izrecno odobrena. Izdaja, push, zunanja sporočila in spremembe dejanskih uporabnikovih projektov ostanejo ločeni koraki. Zunanji sprejemni kriteriji zahtevajo namenske testne račune in naprave, zato se štejejo za dokončane šele po dejanskem preizkusu.


Original document: `runner-step-logs-plan.md`.

## Plan logov posameznih korakov na runnerju

Datum: 2026-10-09. Status: implementirano lokalno, integracijski testi niso bili izvedeni.

### Cilj in potrjena arhitektura

Browser omogoča pregled dogajanja za vsak korak in vsak njegov poskus, med
izvajanjem in po zaključku. Privzeto pokaže človeku berljivo časovnico, dodatno
pa raw dogodke in izhod ukazov, kjer obstajajo.

Uporabnik je potrdil: razbremenimo predvsem disk enginea. Logi so začasno
shranjeni na runnerju, browser jih bere prek enginea na zahtevo. Omrežni promet
branih logov zato še vedno potuje prek enginea.

Engine trajno hrani status, kratek povzetek, poslovne rezultate, podatke o
poskusih in majhen manifest logov. Vsebina logov ne gre v njegovo SQLite bazo,
artefakte ali backup. Obstoječi artefakti ostanejo ločena funkcionalnost.

### Izhodišče iz trenutne kode

* `runner/process.py` zbira stdout in stderr v pomnilniku. Preseganje omejitve
  256 KiB na posamezen stream povzroči napako izvajanja; delni izhod ob timeoutu
  ni ohranjen v rezultatu.
* `runner/executor.py` pošilja stdout in stderr v `StepResult.outputs`.
  `runner/agents.py` dodaja CLI dogodke v `agent_events`. Engine shranjuje rezultate
  v `steps` in `attempts`, runner pa jih hrani tudi v svojem journalu.
* `runner/api_agent.py` ima lastno zanko API klicev in lokalnih orodij, zato zajem
  samo subprocess izhoda ne zadostuje.
* `engine/web/app.js` prikazuje skupni activity in končne povzetke. Vozlišča
  korakov nimajo podrobnega log pogleda.
* Runner danes odpira odhodne povezave. Že obstajajo ID poskusa, generation,
  runner identiteta, journal in browser avtentikacija. Protokol kode je verzija 2.

### 1. Lokalna shramba in pogodba dogodkov

Dodamo `runner/log_store.py`. Shramba je zunaj projektnega workspacea, z zasebnimi
pravicami in ločeno od journala. Imeniki uporabljajo engine identiteto, run ID in
attempt ID, ne uporabniških imen ali poljubnih poti.

Za vsak poskus hranimo append-only JSONL dogodke in manifest. Dogodek vsebuje
`sequence`, UTC čas, `kind`, `level`, `source`, `message` in po potrebi strukturiran
`data`. Manifest vsebuje lokacijo po identiteti, različico formata, razpoložljive
streame, velikost, zadnjo sekvenco, stanje, truncation in rok hrambe.

Human readable in raw sta pogleda nad isto osnovo dogodkov. Raw pomeni izvorne
dogodke ponudnika ali stdout/stderr z odstranjeno občutljivo vsebino. Ne pomeni
nefiltriranih skrivnosti. Pri običajnem shell izhodu je berljivi pogled časovno
označen izhod; ne dodajamo umetne interpretacije vsake vrstice.

Sekvence določajo vrstni red zajema na runnerju. Ne obljubljamo natančnega
izvornega vrstnega reda med ločenima stdout in stderr streamoma.

### 2. Zajem med izvajanjem

Vpeljemo skupen logging context za celoten poskus, tudi pripravo workspacea,
vgrajene Git/Jira/device akcije, notranje iteracije, cleanup in recovery.
Podoperacije dobijo lasten ID za povezovanje začetka in konca.

`run_process` sproti odvaja oba streama v lokalno shrambo. Ločimo omejitev loga
od omejitve izhoda, ki ga koda potrebuje za parsanje JSON ali nadaljnjo obdelavo.
Velik log ne sme sam po sebi povzročiti neuspeha koraka. Po doseženi kvoti še
naprej beremo pipe in zavržemo presežek; označimo izgubo podatkov.

CLI adapterja sproti beležita razpoložljive dogodke in jih pretvorita v berljive
zapise o orodjih, spremembah datotek, ukazih in zaključku. Neznani dogodki
ostanejo dostopni v raw pogledu. API adapter zabeleži klice orodij, trajanje,
rezultate, uporabo tokenov in napake. Ne beležimo zasebnega reasoning procesa,
avtentikacijskih headerjev ali celotnih promptov privzeto.

Končne dogodke zapišemo tudi ob napaki, preklicu, timeoutu in izgubi leasea.
Po sesutju runnerja recovery označi nepopoln poskus; prejšnji zapisi ostanejo.
Okvara log shrambe se jasno prikaže, ne sproži ponovne izvedbe zunanjih učinkov.

### 3. Ločitev logov od rezultatov

Odstranimo diagnostični stdout/stderr in `agent_events` iz privzetih rezultatov,
ki se prenašajo na engine. Ohranimo tipizirane poslovne rezultate, exit code,
status, omejen povzetek in referenco na manifest.

Pred spremembo preverimo uporabo `outputs.stdout` in podobnih polj v workflowih.
Če so izhodni podatki dejanski vhod naslednjega koraka, zahtevamo eksplicitno,
velikostno omejeno rezultatsko pogodbo. Ne brišemo jih avtomatično samo zato,
ker imajo ime stdout. Poljubnega celotnega `outputs` ne kopiramo v log.

### 4. Branje prek enginea

Dodamo avtenticiran odhodni polling runnerja na engine za log zahteve. Runner ne
potrebuje javnega HTTP strežnika ali odprtih vhodnih portov. Polling teče v
ločenem opravilu, zato ne blokira renewal, claim ali dostave rezultata.

Browser kliče npr. `GET /admin/runs/{run}/attempts/{attempt}/logs` z view,
cursor in limit. Engine preveri browser session, pripadnost poskusa runu in
lastništvo runnerja. Runner dobi request ID in identiteto poskusa ter vrne samo
omejen segment. Engine odgovor posreduje brez trajnega shranjevanja; reverse
proxy ne sme zapisovati odzivov v disk cache ali začasne datoteke.

Za MVP uporabimo cursor paging in osveževanje odprtega panela na dve sekundi.
Po zaključku ustavimo polling. Prenos celotnega redigiranega loga je pretočen,
z omejeno velikostjo in preklicem ob zaprtju browser povezave.

Branje zaključenega poskusa ni vezano na veljaven execution lease. Preverja
lastništvo poskusa in trenutno dovoljenje runnerja. Povezava ima timeout,
omejitev sočasnih branj in velikosti frameov; po reconnectu nadaljujemo s cursorjem.
Vsebina se prenaša samo, ko jo uporabnik bere.

### 5. Browser pogled

Klik ali tipkovnična izbira koraka odpre panel z:

* statusom, trajanjem, runnerjem, končnim povzetkom in izbiro poskusa;
* zavihkoma `Activity` in `Raw`, filtrom nivoja, virom in iskanjem;
* sprotnim dopolnjevanjem, gumbom za sledenje koncu in prenosom loga;
* jasnim prikazom nedosegljivega runnerja, potekle hrambe, manjkajočih,
  nepopolnih ali skrajšanih logov.

V prvem koraku iskanje pokriva naložene zapise in je tako tudi označeno.
Velikih logov ne nalagamo v celoti v DOM. Ponovitve in retryji imajo ločene
poskuse. Preskočen ali še nedodeljen korak pokaže razlog oziroma status enginea,
ne namišljenega runner loga. Končne povzetke ohranimo tudi v skupnem activityju.

### 6. Hramba in zaščita

Predlagane začetne nastavitve v `RunnerConfig`:

```yaml
logs:
  directory: logs
  retention_days: 7
  max_total_bytes: 2147483648
  max_attempt_bytes: 52428800
```

Sedem dni teče od zaključka poskusa. Ob pritisku na disk najprej brišemo
najstarejše zaključene loge. Aktivnih ne brišemo; ob doseženi kvoti zajem
skrajšamo in ohranimo prostor za končni status. Redno čiščenje teče na runnerju,
tudi brez povezave z engineom. Reconnect uskladi manifeste in obvestila o izbrisu.

Izbris runa pošlje runnerju trajno, ponovljivo zahtevo za izbris njegovih logov.
Offline runner jo obdela ob ponovni povezavi; retention ostane varovalka.
Engine prune in runner retention sta ločena in dokumentirana.

Redakcija se zgodi pred zapisom, tudi kadar je skrivnost razdeljena med chunkoma.
Uporabimo skupen redactor za loge in rezultate ter pokrijemo tudi konfigurirane
API credential spremenljivke. HTML in ANSI vsebino varno izrišemo kot tekst.
Ne hranimo skrite nefiltrirane kopije. Odstranjevanje znanih skrivnosti ne more
zagotoviti odstranitve vseh osebnih ali občutljivih podatkov iz poljubnega izhoda.

### 7. Faze implementacije in preverjanje

1. Pogodbe, shramba, konfiguracija in redakcija. Preverimo kvote, čiščenje,
   chunk redakcijo, crash recovery in izolacijo poti.
2. Instrumentacija subprocessov, agentov in vgrajenih akcij. Preverimo delne
   loge pri timeoutu/preklicu, velike izhode in poslovne rezultate brez regresij.
3. Kanal runner/engine, manifesti in API. Preverimo avtentikacijo, napačno
   lastništvo poskusa, offline stanje, reconnect, omejitve in sočasna branja.
4. Browser panel. Preverimo tekoči in zaključeni korak, več poskusov, raw
   fallback, tipkovnico in velik log brez zamrznitve prikaza.
5. Migracija in operations dokumentacija. Preverimo, da novi logi niso v engine
   bazi, artefaktih ali backupu; poskus ostane berljiv po restartu runnerja.

Novo zmožnost oglašujemo v registraciji runnerja. Ker spreminjamo pogodbo
rezultatov in transport, uvedemo protokol 3, nadgradimo engine pred runnerji
in uporabimo eksplicitno diagnostiko nezdružljivosti. Stari runi lahko v
rezultatih še vsebujejo loge; pokažemo jih kot legacy podatke. Zgodovine ne
selimo na runner brez posebne migracije in je ne brišemo samodejno.

Sprejemni pogoj: med izvajanjem iz browserja odprem poljuben poskus, vidim
dogajanje in razpoložljiv raw izhod, rezultat pa se pravilno zaključi tudi ob
prekoračitvi log kvote. Po restartu runnerja ohranjeni logi delujejo. Ob
nedosegljivem runnerju ali izteku hrambe browser jasno razloži stanje.

### Omejitev zasnove

Offline runner pomeni začasno nedostopne podrobnosti. Izguba njegovega diska
ali potek hrambe pomeni trajno izgubo teh logov. Engine ohrani status, kratek
povzetek in poslovne rezultate. Za dolgoročni audit bi pozneje dodali ločen
arhiv, kar ni del tega plana.


Original document: `users-organizations-auth-plan.md`.

## Plan uporabnikov, organizacij in prijave

Datum: 2026-10-10. Status: implementacija v repozitoriju je pripravljena; zunanje prijave in celotni integracijski scenariji še niso preverjeni s produkcijskimi poverilnicami.

### Trenutno izvedeno

V kodi so podatkovne migracije za uporabnike, preverjene emaile, organizacije,
članstva, vloge, vabila, preklicljive seje in login tokene. Dodani so
passwordless email tok prek Brevo API, OAuth/OIDC callbacki za Google in
GitHub, Bootstrap začetnega ownerja, organizacijsko filtriranje obstoječega
API-ja, UUID identitete novih projektov in omejevanje runner claimov na
organizacijo. UI omogoča prijavo, izbiro in ustvarjanje organizacije, vabila,
pregled članov ter upravljanje vlog in vabil.

Sintaksa Python in JavaScript, Ruff preverjanje z izključitvijo dveh
predobstoječih pravil ter `git diff --check` so uspešni. Projektni testi imajo
71 uspešnih in 2 neuspešna testa. Neuspešna sta obstoječa pričakovanja za
izpis stdout v rezultatu izvajanja ter izjemo pri prekoračitvi izhodne omejitve;
oba se dotikata predhodnega runner/log dela in nista bila spremenjena v tej
implementaciji računov. Brevo, Google in GitHub zahtevajo prave podatke za
končni sprejemni test. Runnerji so omejeni v engine bazi; ločene izolacije
procesov in OS uporabnikov za več organizacij ni mogoče zagotoviti samo s to
spremembo aplikacije.

### Potrjen obseg

1. Registracija je odprta za vse uporabnike z verificiranim emailom.
2. Vsak nov uporabnik dobi lastno privzeto organizacijo in vlogo owner.
3. Uporabnik lahko ustvari dodatne organizacije. Število organizacij in projektov nima produktne omejitve; zaščita pred zlorabo ostaja potrebna.
4. Organizacije podpirajo vabila in vloge owner, admin, member.
5. Obstoječi projekti pripadajo privzeti organizaciji začetnega lastnika.
6. Runner pripada eni organizaciji in izvaja samo njene odobrene projekte.
7. Prijava podpira email povezavo, Google in GitHub. Zunanji prijavi imata za začetek dummy konfiguracijo.
8. Projektni workflowi, profili, akcije, triggerji, izvajanja, retention, logi in artefakti ohranijo sedanjo funkcionalnost.
9. Skrivnosti sodijo v zasebno splošno konfiguracijo enginea, nikoli v projektni `.aorch/config.yaml` ali Git.

To je model organizacij po vzoru GitHub, ne njegova popolna kopija. Tudi uporabnikovi lastni projekti pripadajo njegovi organizaciji. GitHub organizacij in repozitorijev ob prijavi ne uvažamo.

### Izhodišče iz trenutne kode

* `src/aorch/engine/api.py`: skupen admin token, podpisana browser seja, globalni `/admin/state` in `/admin/events`; ni uporabnikov ali preverjanja članstva.
* `src/aorch/engine/storage.py`: SQLite shema verzije 4; `projects.name` je globalni primarni ključ. Workflowi, revizije, izvajanja in druge tabele projekt referencirajo kot besedilo.
* `src/aorch/engine/runtime.py`: runner ima globalni ID, hash tokena in JSON seznam dovoljenih projektov.
* `src/aorch/config/service.py`: stroga konfiguracija enginea, skrivnosti iz datotek z dovoljenji 0600. Trenutni loader razrešuje samo vrhnja polja tipa Path.
* `src/aorch/engine/web/app.js`: izbira projekta in API klici temeljijo na globalnem imenu projekta.
* `.gitignore` že izključuje `.runtime/`, `.token` in `.key`, ne pa poljubnih zasebnih YAML ali `.env` datotek.

Repozitorij že vsebuje lokalne spremembe. Implementacija mora graditi na trenutnem stanju in jih ohraniti.

### 1. Podatkovni model in imena

| Entiteta | Ključni podatki in omejitve |
|---|---|
| `users` | UUID, unikaten normaliziran username, prikazno ime, status, `default_organization_id`, čas nastanka |
| `user_emails` | user ID, email, normaliziran unikaten email, verified timestamp, primary flag |
| `auth_identities` | user ID, provider, provider subject; UNIQUE(provider, subject) |
| `organizations` | UUID, unikaten normaliziran slug, prikazno ime, creator ID, čas nastanka |
| `organization_memberships` | organization ID, user ID, role; UNIQUE(organization_id, user_id) |
| `organization_invitations` | organizacija, ciljni email, role, hash tokena, rok, inviter, accepted/revoked timestamp |
| `projects` | stabilen UUID, organization ID, slug, obstoječi podatki; UNIQUE(organization_id, normalized_slug) |
| `sessions` | hash naključnega session tokena, user ID, rok veljavnosti, čas nastanka in preklica |
| `login_tokens` | hash naključnega tokena, email, namen, rok veljavnosti, čas uporabe |
| `oauth_transactions` | hash state, browser binding, provider, nonce/PKCE podatki, namen login/link, rok |
| `audit_events` | actor, organizacija, dejanje, target ID, čas; brez credentialov in login povezav |
| `runners` | obstoječi podatki, organization ID in dovoljenja do stabilnih project ID-jev |

Imena organizacij za naslov in URL predstavljajo globalno unikatni slugi, brez razlikovanja velikih in malih črk. Prikazno ime je lahko poljubno in ni identifikator. Predlagano pravilo: 1 do 63 ASCII črk, številk ali vezajev, brez začetnega ali končnega vezaja; rezervirana sistemska imena so zavrnjena. Unikatnost zagotavlja baza, tudi pri sočasnem ustvarjanju.

Username uporablja isto normalizacijo. Ob prvi prijavi onboarding predlaga username iz identitete, uporabnik potrdi prosto ime. Privzeta organizacija predlaga isti slug; ob zasedenosti onboarding zahteva drugo prosto ime. Iz emaila ne objavljamo samodejno osebnih podatkov v URL.

Prvo ustvarjanje uporabnika, preverjenega emaila, identitete, organizacije in owner članstva je ena transakcija. Ponovljeni ali sočasni callbacki ne smejo ustvariti dodatnih organizacij. Nedokončan onboarding še ne omogoča projektnih operacij.

Projekt je unikaten znotraj organizacije. Dve organizaciji lahko imata projekt `backend`. Vse notranje reference, cache ključi, engine checkout poti in runner dovoljenja uporabljajo project UUID. URL in UI prikazujeta `organization/project`. `.aorch/config.yaml` ohrani obstoječi `name` in format; registrirani naziv projekta ostaja ločen od notranje identitete.

### 2. Članstvo in dovoljenja

| Operacija | owner | admin | member |
|---|---|---|---|
| Ogled vseh projektov organizacije, izvajanj, logov in artefaktov | da | da | da |
| Zagon, preklic, refresh in obstoječe operacije workflowov | da | da | da |
| Ustvarjanje projektov | da | da | ne |
| Brisanje projektov, izvajanj in spreminjanje retention | da | da | ne |
| Vabila member uporabnikov in odstranitev member članov | da | da | ne |
| Upravljanje runnerjev in njihovih projektnih dovoljenj | da | da | ne |
| Dodelitev admin/owner vlog, upravljanje owner članstva | da | ne | ne |
| Nastavitve in brisanje organizacije | da | ne | ne |

Vsi projekti so zasebni za organizacijo. Projektnih izjem in posebnih ekip v prvi izvedbi ni. Member lahko izvaja kodo prek workflowov, zato mora biti zaupanja vreden član organizacije. Platformske skrivnosti niso dostopne nobeni organizacijski vlogi.

Organizacija ima lahko več ownerjev, zadnjega ownerja ni dovoljeno odstraniti ali degradirati. Privzete organizacije ni dovoljeno izbrisati, dokler je privzeta; izbrati je treba drugo organizacijo, kjer je uporabnik owner. Brisanje organizacije je dovoljeno šele, ko nima projektov in runnerjev, da se izognemo prikritemu množičnemu brisanju.

Vabilo velja predlagano 7 dni in je vezano na preverjen email. Sprejem zahteva prijavo s tem emailom ali dokazano povezanim preverjenim emailom. Vabilo samo ne ustvarja članstva. Token je enkraten, preklicljiv in shranjen samo kot hash. Admin ne more povabiti ownerja ali admina. Novi povabljeni uporabnik ob registraciji dobi tudi svojo privzeto organizacijo.

### 3. Izolacija API, podatkov in runnerjev

Uvedemo skupne funkcije `current_user`, `require_membership` in preverjanje dovoljenja za operacijo. Članstvo se preverja na strežniku ob vsakem zahtevku; organizacija iz UI ni dokaz dostopa.

Predlagane poti:

* `/auth/providers`, `/auth/me`, `/auth/logout`.
* `/auth/email/request`, `/auth/email/confirm`.
* `/auth/google/start`, `/auth/google/callback`.
* `/auth/github/start`, `/auth/github/callback`.
* `/auth/onboarding`, `/auth/identities` in ločena prijava za povezovanje identitete.
* `/api/organizations` ter poti za memberships in invitations.
* `/api/organizations/{organization_id}/projects`.
* `/api/projects/{project_id}/...` za workflowe in projektne operacije.
* Poti za runs, artifacts in logs lahko ohranijo stabilne ID-je, vendar dovoljenje vedno razrešijo prek pripadajočega projekta in organizacije.

Globalni pregled nadomestimo z organizacijskim pregledom. SSE dogodke filtriramo v poizvedbi, pred pošiljanjem. Enako velja za napake konfiguracije, resource locks, sezname runnerjev in log proxy zahteve. Odprti SSE in log stream se zaključi ob poteku seje ali izgubi članstva. Poskusi dostopa do tujih ID-jev ne razkrivajo njihove vsebine ali obstoja.

Globalni `/admin/reload` postane operacija posamezne organizacije ali projekta. Skupni admin token po migraciji ne ostane browser obvoz mimo članstva. Operaterski bootstrap je ločen CLI postopek z lokalnim dostopom do baze.

Runner tokeni ostanejo strojna avtentikacija. Organizacija runnerja je shranjena v bazi; runner je ne more spremeniti z registracijo. Claim, leases, events, artifacts, log proxy in retention ukazi morajo preveriti organizacijo ter dovoljen projekt. Dovoljenja preselimo iz JSON imen v relacijo runner/project. Obstoječi globalni runner ID-ji lahko za zdaj ostanejo stabilni.

Ne spreminjamo pomena globalnih fizičnih resource locks: isti fizični vir mora ostati zaščiten pred sočasno uporabo. Članom razkrijemo samo njihove podatke o virih. Project resource locks uporabljajo project UUID.

Ločen runner v bazi sam ne izolira procesov. Runnerji različnih organizacij morajo imeti ločene uporabnike OS ali izolirana okolja, diske in poverilnice. Skupnega izvajanja nezaupanja vrednih organizacij na istem nezaščitenem hostu ta izvedba ne zagotavlja.

Engine pri odprti registraciji ne sme poljubno uporabljati lokalnih poti ali skupnih SSH/Git credentialov za uporabniško izbrane repozitorije. Lokalno registracijo projektov omejimo na operator CLI; Git fetch mora preprečiti dostop do notranjih/metapodatkovnih naslovov in izvajanje nevarnih lokalnih Git nastavitev. Za zasebne repozitorije potrebujemo organizacijsko vezane Git poverilnice. GitHub prijava ne pomeni dovoljenja za kloniranje repozitorijev. Pred javnim zagonom je to del iste meje zaupanja kot izolacija runnerjev.

### 4. Passwordless prijava in seje

Predlagani tok:

1. Uporabnik vnese email. API vedno vrne isti nevtralen odgovor, ne glede na obstoj računa.
2. Ustvarimo kriptografsko naključen enkratni token z veljavnostjo 15 minut, shranimo samo hash, namen in ciljni email.
3. Brevo pošlje povezavo iz `no-reply@runestra.app`. URL sestavimo iz konfiguriranega `public_base_url`, nikoli iz nepreverjenega Host headerja.
4. Odpiranje povezave pokaže potrditveno stran. GET še ne porabi tokena, da ga email scanner ne porabi pred uporabnikom.
5. Potrditev s POST, zaščitena z browser transakcijo in preverjanjem izvora, atomsko porabi token in ustvari ali prijavi uporabnika. Povezava deluje tudi v drugem browserju ali napravi. Stran pokaže ciljni email, da zmanjša možnost neželene prijave v tuj račun.
6. Prva prijava nadaljuje v onboarding in atomsko ustvari privzeto organizacijo.

Login stran nima zunanjih analitičnih skript; uporablja `Referrer-Policy: no-referrer` in `Cache-Control: no-store`. Token po obdelavi odstranimo iz browser URL. Iz access logov, exceptionov in Brevo diagnostike odstranimo URL tokene. Za auth emaile izključimo click tracking, če je omogočen.

Omejimo pošiljanje po IP in emailu, uvedemo cooldown, omejitev poskusov potrditve in kratke timeoute za Brevo. Ne izvajamo slepega ponavljanja po nejasnem rezultatu pošiljanja. Evidentiramo delivery napako brez vsebine povezave ali API keyja. Brevo sprejem sporočila še ne pomeni dostave v inbox.

Browser dobi samo naključen session token v HttpOnly cookieju. Seja je v bazi in preklicljiva, predlagano velja 8 ur. Produkcija zahteva HTTPS, Secure cookie, SameSite=Lax za OAuth povratne GET klice in CSRF zaščito vseh mutacij. Ob prijavi rotiramo sejo, ob odjavi jo prekličemo. V cookie ne shranjujemo vlog kot trajnega vira dovoljenj.

### 5. Google in GitHub

Uporabimo vzdrževano OAuth/OIDC knjižnico, predlagano Authlib, skupaj z obstoječim HTTPX. Ne implementiramo lastnega preverjanja podpisov JWT.

Google: Authorization Code tok, scopes `openid email profile`, state, nonce in PKCE. Preverimo podpis ID tokena, issuer, audience, expiration, nonce in `email_verified`. Identiteta je Googlov `sub`, ne email ali prikazno ime.

GitHub: OAuth App Authorization Code tok, state in PKCE skladno z aktualno podporo; minimalni scope `user:email`. Identiteta je stabilni GitHub user ID. Email preberemo prek `/user/emails` in sprejmemo samo preverjen naslov; ob več naslovih uporabnik izbere preverjen naslov za račun. Ne zahtevamo dostopa do repozitorijev ali GitHub organizacij. Dostopni token po identifikaciji zavržemo, ne hranimo refresh tokenov za prijavo.

Callback transakcija je kratkotrajna, enkratna in vezana na browser ter ponudnika. Redirect po prijavi je dovoljen samo na preverjene lokalne poti. Callback napake ne vračajo credentialov ali ponudnikovih tokenov.

En račun lahko ima več prijavnih identitet. Če nov ponudnik vrne email obstoječega računa, računov ne združimo avtomatsko. Uporabnik se najprej prijavi v obstoječi račun in izrecno poveže ponudnika s svežo prijavo. Konflikt subject ID-ja zavrnemo. Odstranitev zadnje uporabne prijavne metode ni dovoljena.

Dummy konfiguracija ne simulira uspešne zunanje prijave. Ponudnika sta `enabled: false`; UI ju pokaže kot še nenastavljena ali skrije. Lokalni testi uporabljajo ponarejene provider odgovore brez klicev pravih ponudnikov. Dejanski Google/GitHub tok je preverljiv šele s pravimi client podatki in registriranimi callback URL-ji.

### 6. Splošna konfiguracija in Brevo

Predlagani format razširitve zasebnega `engine.yaml`, ni še veljavna konfiguracija trenutnega loaderja:

```yaml
auth:
  public_base_url: "https://REPLACE_WITH_PUBLIC_HOST"
  registration_enabled: true
  secrets_file: "auth-secrets.yaml"
  session_ttl_seconds: 28800
  magic_link_ttl_seconds: 900
  invitation_ttl_seconds: 604800
  google:
    enabled: false
    client_id: "DUMMY_GOOGLE_CLIENT_ID"
    callback_path: "/auth/google/callback"
  github:
    enabled: false
    client_id: "DUMMY_GITHUB_CLIENT_ID"
    callback_path: "/auth/github/callback"
email:
  provider: "brevo_api"
  from_email: "no-reply@runestra.app"
  from_name: "Runestra"
```

Zasebni `auth-secrets.yaml`, dovoljenja 0600:

```yaml
brevo_api_key: "REPLACE_WITH_BREVO_API_KEY"
google_client_secret: "DUMMY_GOOGLE_CLIENT_SECRET"
github_client_secret: "DUMMY_GITHUB_CLIENT_SECRET"
```

Sejo bomo hranili na strežniku. Obstoječi `session_key_file` ohranimo samo, če je potreben za podpisovanje začasnega browser stanja; v tem primeru tudi ta datoteka ostane zasebna.

Implementacija pripravi verzionirani `engine.example.yaml` in `auth-secrets.example.yaml` izključno s placeholderji. Dejanska konfiguracija gre prednostno izven checkouta, npr. `~/.config/runestra/engine.yaml` in `~/.config/runestra/auth-secrets.yaml`, ali pod že ignorirani `.runtime/`. Dodamo natančna ignore pravila za zasebne datoteke in `.env`, z izjemo primerov. Ne ignoriramo vseh YAML datotek, ker so workflowi namenjeni Git-u.

Konfiguracijo validiramo strogo. Razreševanje relativnih poti razširimo tudi na gnezdene modele. Za provider skrivnosti ne uporabljamo trenutne generične zahteve najmanj 32 znakov; pravila ponudnikov so drugačna. Pri vključenem providerju manjkajoča ali dummy skrivnost povzroči jasno napako. Prikaz konfiguracije redigira skrivnosti. Skrivnosti se ne prenašajo na runnerje, v projektne snapshote, browser ali loge.

Za Brevo API izvedbo potrebujemo:

* API key z dovoljenjem za pošiljanje transakcijskih emailov, nastavljen v zasebni konfiguraciji.
* V Brevo konfiguriranega in preverjenega pošiljatelja `no-reply@runestra.app` ter avtenticirano domeno `runestra.app` z DNS zapisi po Brevo navodilih.
* Končni javni HTTPS naslov aplikacije za login povezave in OAuth callbacke. Ime domene aplikacije ni nujno enako domeni pošiljatelja.
* Omogočeno pošiljanje transakcijskih emailov in dovolj razpoložljive kvote v računu.

SMTP uporabnika, gesla in Brevo template ID-ja ne potrebujemo. Vsebino prijavnih in vabilnih emailov pripravimo v aplikaciji. Pošiljanje uporablja `POST https://api.brevo.com/v3/smtp/email` z `api-key` headerjem. Če bi kasneje izbrali SMTP, bi potrebovali SMTP login in SMTP key; Brevo API key ni SMTP key.

Google nastavitev zahteva Web OAuth client, consent konfiguracijo, client ID/secret ter natančno registriran `{public_base_url}/auth/google/callback`. GitHub zahteva OAuth App, client ID/secret, homepage URL in callback `{public_base_url}/auth/github/callback`. Lokalno in produkcijsko konfiguracijo vodimo ločeno.

### 7. Migracija obstoječe namestitve

1. Ustavimo dodeljevanje novih del, zaključimo ali kontrolirano ustavimo aktivna izvajanja in naredimo usklajen backup SQLite, artefaktov ter zasebne konfiguracije. Migracija ne teče med aktivnim delom.
2. Operator z lokalnim CLI določi verificirajoči email začetnega lastnika, username in slug njegove privzete organizacije. Email se ne ugiba iz Git nastavitev. Lastnik ga potrdi ob prvi passwordless prijavi.
3. Verzijska migracija ustvari uporabnika s pending emailom, njegovo privzeto organizacijo in owner članstvo ter dodeli project UUID-je vsem obstoječim projektom.
4. Prepišemo reference v workflows, revisions, occurrences, items, runs, workflow_retention in project resource locks. Runner dovoljenja pretvorimo v relacije po project UUID-ju in obstoječe runnerje dodelimo isti organizaciji. Preverimo tudi revizijske sestavljene ključe in vse lookup poizvedbe.
5. Obstoječi run, attempt, item in artifact ID-ji ostanejo stabilni. Revizijske snapshote ohranimo nespremenjene zaradi hashov; pripadajoči project ID je metapodatek izven paketa. Log poti in manifests ohranimo, ker temeljijo na run/attempt identitetah.
6. Engine cache poti preselimo ali varno ponovno ustvarimo po project UUID-ju. Ohranimo uporabniške lokalne repozitorije in runner workspacee.
7. Posodobimo `init-local`, enqueue, runner approval CLI, dokumentacijo in protokol. Če nova pogodba dodaja obvezne project/org podatke, zvišamo protocol version in zavrnemo nezdružljive runnerje; starih projektnih imen ne interpretiramo dvoumno.
8. Odstranimo skupno browser prijavo z admin tokenom. Pending lastniški email je rezerviran za bootstrap račun; OAuth ne sme prevzeti tega računa brez njegove passwordless potrditve.
9. Preverimo števila zapisov, foreign keys, zgodovino, dostop in runner dodelitve. Ponoven zagon migracije je varen. Ob neuspehu obnovimo celoten backup s prejšnjo verzijo aplikacije.

Pred izvedbo potrebujemo točen lastniški email, username in organizacijski slug. Ti podatki ter produkcijski URL so deployment konfiguracija, ne razlog za ustavitev priprave kode.

### 8. Faze implementacije in kriteriji zaključka

| Faza | Dostavljeno | Preverjanje |
|---|---|---|
| 1. Model in migracija | Nove entitete, stabilni project ID-ji, bootstrap CLI | Migracija kopije V4 baze, enaki zgodovinski podatki, transakcijski rollback, podvojena imena med organizacijami |
| 2. Članstvo in izolacija | Organizacijski API, role policy, runner scope | Dve organizaciji, negativni testi za vsak podatkovni in mutacijski endpoint, claim in log proxy |
| 3. Konfiguracija in email | Zasebni secrets loader, primeri, Brevo adapter | Redakcija, 0600, ignore pravila, invalid config, timeout/429/send failure brez razkritja tokena |
| 4. Passwordless in onboarding | Enkratni tokeni, uporabniki, privzeta organizacija, strežniške seje | Expired/replayed/sočasni tokeni, scanner GET, cross-device potrditev, session revoke, CSRF, rate limit |
| 5. Google/GitHub | Provider tokovi, account linking, dummy konfiguracija | State/nonce/PKCE, nepravilni issuer/audience, neverificiran email, napačen browser, linking konflikt |
| 6. Organizacije in vabila | Ustvarjanje organizacij, članstvo, vabila | Sočasna rezervacija sluga, expiry/revoke, napačen email, zadnji owner, admin ne more eskalirati vloge |
| 7. Browser UI | Login, onboarding, org selector, člani, nastavitve, projekti | Celoten uporabniški tok, prazne organizacije, preklop org brez starih podatkov, logout in izguba članstva |
| 8. Regresija in rollout | Dokumentacija, lokalni setup, upgrade navodila | Obstoječi workflowi, scheduler, recovery, retention, runner logi in artefakti; lokalni test dveh organizacij |

UI pri preklopu organizacije počisti izbran projekt/run, cache in star SSE stream. Nastavitve organizacije vsebujejo člane, vabila in runnerje; profil vsebuje povezane načine prijave. Videz sledi obstoječemu design systemu.

Zaključek lokalne implementacije zahteva `uv run pytest -q`, `uv run ruff check src tests scripts` in preverjanje browser tokov. Google, GitHub in dostavo prek Brevo označimo kot preverjene zunanje integracije šele po sprejemnem testu s pravimi nastavitvami. Dummy vrednosti in simulirani odgovori tega ne dokazujejo.

To je sprememba osnovnega modela dostopa in migracija podatkov, zato je ne obravnavamo kot majhen dodatek login strani. Javni rollout sledi šele po preverjeni izolaciji organizacij in izvajalnih okolij.

### Uradni viri

* [Brevo transactional email API](https://developers.brevo.com/docs/send-a-transactional-email)
* [Brevo SMTP integration](https://developers.brevo.com/docs/smtp-integration)
* [Brevo domain authentication](https://help.brevo.com/hc/en-us/articles/12163873383186-Authenticate-your-domain-with-Brevo-Brevo-code-DKIM-DMARC)
* [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect)
* [GitHub OAuth authorization](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)
