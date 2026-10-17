# Načrt razširitve: reasoning effort pri agent korakih

Datum: 2026-10-15. Status: predlog, še ni implementirano.

Uporabnik je potrdil nastavitev v profilu in preglasitev na posameznem agent
koraku. Predlagani obseg zajema Codex CLI, Claude CLI in OpenAI Responses API.
Obseg adapterjev je predpostavka, ker na to vprašanje še ni odgovora.

## Cilj in trenutno stanje

Uporabnik lahko za enostaven korak nastavi manjši reasoning effort, za zahtevno
implementacijo ali pregled pa večjega, tudi če koraka uporabljata isti profil.
Runestra nastavitev preveri, prenese do izvajalca in zabeleži, kaj je zahtevala.

Pregledana osnova je commit `1cdceba` in trenutna delovna kopija. Obstoječe
nepovezane spremembe v delovni kopiji niso del tega načrta.

| Področje | Trenutno stanje |
|---|---|
| Konfiguracija | `AgentProfile` v `src/aorch/config/models.py` ima `model`; `Step` nima modela ali reasoning effort. Korak izbere profil. |
| CLI adapterja | `src/aorch/runner/agents.py` prebere profil iz paketa in posreduje `--model`. |
| OpenAI API | `src/aorch/runner/api_agent.py` sestavi vsak klic `/responses` brez `reasoning`. |
| Interni agenti | `coding.repair` v `src/aorch/extensions/git.py` ustvari implementacijski in pregledovalni agent korak iz dveh profilov. |
| Združljivost | Transportni protokol je 5; preverjanje združljivosti inline promptov je podvojeno v izbiri runnerja in prikazu blokade. |
| Paket | Snapshot vsebuje celotno konfiguracijo; runner jo v `verify_package` ponovno strogo validira. |

## Predlagana konfiguracijska pogodba

Dodati neobvezno polje `reasoning_effort` v `AgentProfile` in `Step`.
Na koraku je dovoljeno samo pri `type: agent`. Model ostane v profilu;
preglasitev modela na koraku ni del te razširitve.

Pravila razrešitve:

1. Konkretna vrednost na koraku preglasi profil.
2. Manjkajoča vrednost ali YAML `null` na koraku deduje vrednost profila.
3. Vrednost `default` izrecno izključi podedovano nastavitev. Adapter ne pošlje
   argumenta oziroma API polja za effort.
4. Profil brez nastavitve, z `null` ali z `default` prepusti izbiro izvajalcu.
5. `none` je konkretna raven pri modelih, ki jo podpirajo. Ni sinonim za
   `default` ali YAML `null`.

Javna pogodba naj pozna `default`, `none`, `minimal`, `low`, `medium`, `high`,
`xhigh`, `max` in `ultra`. To je skupni besednjak, ne obljuba, da vsak adapter
ali model podpira vse vrednosti. Brez številčnih ravni in brez splošne pretvorbe
`max` v `xhigh` ali podobnih približkov.

Predlagani primer za projektno konfiguracijo različice 2. Trenutni validator
polja `reasoning_effort` še zavrne. Po implementaciji naj bo podpora na voljo
tudi v obstoječih različicah 1 in 3, brez nove različice oblike projekta.

```yaml
version: 2
name: effort-example
profiles:
  engineer:
    adapter: codex
    authentication: local
    reasoning_effort: medium
workflows:
  review:
    steps:
      inspect:
        type: agent
        profile: engineer
        prompt_text: Inspect the workspace and summarize the implementation.
        reasoning_effort: low
      review:
        type: agent
        needs: [inspect]
        profile: engineer
        prompt_text: Review the implementation for correctness and edge cases.
        reasoning_effort: high
```

Za ponovljive rezultate priporočamo ekspliciten `model` v profilu. Kot danes
ostane pri CLI adapterjih neobvezen. Brez modela validacija ne more vnaprej
zagotoviti podpore izbrani ravni.

## Izvajanje in validacija

Uvesti skupno funkcijo za razrešitev efektivne konfiguracije agenta. Vrne novo
konfiguracijo in izvor effort nastavitve, `step`, `profile` ali `runtime_default`.
Ne spreminja skupnega profila ali nespremenljivega paketa. Uporabiti jo morajo
vsi adapterji in interni klici iz `coding.repair`.

| Adapter | Prenos konkretne vrednosti | Preverjanje |
|---|---|---|
| Codex CLI | `--config model_reasoning_effort="high"`, kot en argument za vrednost konfiguracije | Preveriti nameščeni CLI in razpoložljive podatke o modelu. Ravni so odvisne od modela in odjemalca. |
| Claude CLI | `--effort high` | Trenutni CLI pozna `low`, `medium`, `high`, `xhigh`, `max`; druge konkretne ravni zavrniti. Podpora modela je dodatna omejitev. |
| OpenAI API | `"reasoning": {"effort": "high"}` v vsakem klicu `/responses` | API pozna modelno odvisne ravni do `max`; `ultra` v prvem obsegu zavrniti. |

`default` se porabi pri razrešitvi in se nikoli ne pošlje ponudniku. Obstoječe
konfiguracije brez nastavitve ohranijo dosedanje argumente in API zahteve.

Validacija poteka na dveh ravneh:

1. Lokalno preveriti zapis, tip koraka, adapter in efektivno kombinacijo
   profila ter koraka. Napaka naj navede pot v konfiguraciji, adapter, model
   in zahtevano vrednost. Preveriti tudi profile v `coding.repair`.
2. Na runnerju pred zagonom preveriti podporo CLI možnosti brez modelnega klica.
   Razpoložljive podatke o podpori modela uporabiti, kjer obstajajo. Če podpore
   modela ni mogoče zanesljivo preveriti lokalno, je avtoritativen odziv
   ponudnika. Napaka se vrne jasno; Runestra ne ponovi klica brez effort.

Ne uvajati lastnega popolnega kataloga modelov. Tak katalog bi hitro zastaral.
Podporo novim ravnem dodajamo z dokumentiranim adapterjevim preverjanjem.

Omejitev: zunanji CLI ali organizacijska politika lahko raven omeji. Claude to
izrecno dokumentira. Preneseni argument ni dokaz dejansko uporabljene ravni.
Zabeležiti zahtevano raven in morebitno raven, ki jo izvajalec dejansko poroča,
ločeno. Kadar zaznamo neujemanje, korak ne sme poročati, da je bila zahtevana
raven potrjeno uporabljena. Popolnega preverjanja pri vseh modelih ni mogoče
obljubiti samo s predajo CLI argumenta.

`coding.repair` v prvi izdaji deduje effort ločeno iz `implement_profile` in
`review_profile`. Nova vhodna polja akcije za preglasitev po vlogah niso potrebna.
Njena različica pogodbe zato ostane nespremenjena.

## Združljivost in uvedba

Predlagam transportni protokol 6 kot jasno mejo podpore. To sledi obstoječemu
vzorcu za inline prompte in ne zahteva novega sistema capability pogajanja.
Različica ovojnice paketa ostane 1; ni ista stvar kot transportni protokol.

1. Posodobiti pogodbe registracije in lease, API, runner ter preverjanja ob
   povezavi. Novi runner zahteva novi engine. Stari runnerji se lahko še
   registrirajo po dosedanjih pravilih.
2. Nenastavljenih novih polj ne vključiti v serializirano konfiguracijo.
   Obstoječi paketi brez effort morajo ostati razumljivi starejšim runnerjem;
   samo dodajanje polj z vrednostjo `null` ni združljivo s strogo validacijo.
   Ne spreminjati splošne serializacije vseh obstoječih `null` polj.
3. Prisotnost nove nastavitve kjerkoli v paketu zahteva runner protokola 6.
   To vključuje neuporabljen profil, drug workflow, `default` in profile
   akcije `coding.repair`. Runner namreč validira celoten paket.
4. Izločitev nezdružljivih runnerjev izvesti pred prevzemom in pred vezavo
   izvajanja na runner. UI naj pokaže konkreten razlog čakanja.
5. Ista funkcija mora odločati o kompatibilnosti v `Store.claim` in v
   `RuntimeMixin.project_data`, da prikaz in dejanska izbira ne odstopata.
6. Ob dvigu protokola ohraniti podporo inline promptom na runnerjih protokola
   5 in 6. Trenutnega preverjanja enakosti s `PROTOCOL_VERSION` ne prenesti
   neposredno, ker bi s tem neupravičeno izločili runnerje protokola 5.

Vrstni red uvedbe: engine, runnerji, nato konfiguracije z effort. Obstoječih
shranjenih revizij ne prepisujemo. Umik funkcionalnosti pomeni uporabo prejšnje
konfiguracije oziroma odstranitev novih polj in osvežitev projekta.

## Koraki izvedbe

| Korak | Spremembe in rezultat |
|---|---|
| 1. Pogodba in razrešitev | `config/models.py`, po potrebi `config/loader.py`, skupna funkcija za efektivno konfiguracijo; validacija dedovanja, `default` in adapterjev. |
| 2. Izvajalci | `runner/agents.py`, `runner/api_agent.py`, preverjanje CLI podpore; effort v vseh API iteracijah in internih agent klicih. |
| 3. Združljivost | `contracts/__init__.py`, `config/__init__.py`, `engine/storage.py`, `engine/runtime.py`, `engine/api.py`, `runner/client.py`, pregled `runner/package.py`; protokol 6 in skupna pravila izbire. |
| 4. Diagnostika | Obstoječi agent dogodki z adapterjem, modelom, zahtevano ravnjo in izvorom nastavitve. Pri `coding.repair` tudi vloga. Dejanska raven samo, če jo izvajalec poroča. |
| 5. Dokumentacija in sheme | Posodobiti `docs/profiles-and-actions.md`, `docs/project-yaml.md`, navodila za nadgradnjo in izvoz `schemas/project-v*.schema.json`, vključno z import shemo. |
| 6. Preverjanje | Ciljni testi spodaj, nato obstoječa celotna Python zbirka, statični pregledi, validacija primerov in pregled razlik generiranih shem. |

Nov konfiguracijski obrazec v spletni konzoli ni potreben za ta obseg.
Nastavitev je del YAML konfiguracije in obstoječega dnevnika izvajanja.

## Sprejemni kriteriji

- Isti profil lahko uporabita dva koraka z različnima effort nastavitvama;
  preglasitev enega ne vpliva na drugega, tudi ob sočasnem izvajanju.
- Brez preglasitve korak deduje profil. `default` na koraku onemogoči
  podedovano vrednost; `null` deduje; `none` ostane konkretna vrednost.
- Neveljavna raven, napačen tip in effort na neagent koraku se zavrnejo.
- Codex in Claude prejmeta pričakovani argument brez shell interpolacije;
  brez effort argumenta ostaneta dosedanja klica nespremenjena.
- OpenAI prejme `reasoning.effort` v vsakem obratu z uporabo orodij. Test s
  simuliranim HTTP transportom preveri tudi izpustitev polja in odziv ob
  nepodprti kombinaciji. Ni samodejnega ponovnega klica brez nastavitve.
- API omejitve časa, stroškov in tokenov ostanejo veljavne. Reasoning tokenov
  ne prištevamo dvakrat; preveriti ponudnikovo poročanje porabe in odziv ob
  nedokončanem odgovoru zaradi prenizkega `max_output_tokens`.
- `coding.repair` uporabi effort pravega profila pri obeh vlogah in v vseh
  ponovitvah, brez novih vhodnih polj akcije.
- Runner protokola 5 ne prevzame nobenega paketa z novo nastavitvijo;
  protokol 6 ga lahko. To velja tudi za effort v neuporabljenem profilu ali
  drugem workflowu. Prikaz blokade se ujema z izbiro runnerja.
- Stare konfiguracije in stari nespremenljivi paketi še delujejo. Novo
  serializirani paketi brez effort ne dobijo novih `null` polj.
- Inline prompti še delujejo na protokolu 5 po uvedbi protokola 6.
- Sheme in validator se ujemajo; primeri različic 1, 2 in 3 z importi so
  preverjeni z `aorch validate`; relativne povezave so veljavne.

Testi sodijo predvsem v `tests/test_configuration.py`, `tests/test_extensions.py`,
`tests/test_engine.py` in `tests/test_api_runner.py`. Za argumente CLI uporabiti
nadomestni proces, za API obstoječi `httpx.MockTransport`. Plačljivi modelni klici
niso potrebni za avtomatizirane teste. Pred izdajo opraviti še kratek omejen
preizkus na dejanskih podprtih CLI različicah in izbranih modelih; izid ločiti
od samega preverjanja argumentov.

## Ocena obsega

Za AI podprto implementacijo ocenjujem 8 do 14 ur aktivnega dela, vključno s
pregledom, testi, dokumentacijo in integracijo. To je približno 1 do 2 delovna
dneva, če dejanski CLI preizkusi ne odkrijejo dodatnih omejitev. Največ
negotovosti je pri modelno odvisni podpori in zunanjih omejitvah effort, ne pri
dodajanju YAML polja. Ocena ne vključuje novega urejevalnika, kataloga modelov
ali obsežne primerjave kakovosti in stroškov modelov.

## Preverjeni zunanji viri

Pregledano 2026-10-15. Lokalno sta nameščena `codex-cli 0.162.1` in
`Claude Code 2.1.296`; pregled pomoči potrdi konfiguracijski argument pri Codex
in `--effort` pri Claude. Modelni klici niso bili izvedeni.

- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference):
  `model_reasoning_effort`; razpoložljive ravni so odvisne od modela in odjemalca.
- [OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning):
  `reasoning.effort`, modelno odvisne ravni in poraba reasoning tokenov.
- [Claude Code model configuration](https://code.claude.com/docs/en/model-config#effort-level):
  `--effort`, podpora po modelih in organizacijske omejitve.
