# Specifikacija PR ponudnikov: Azure DevOps in Bitbucket

Datum zasnove: 2026-10-12. Status: predlog, ni implementirano v tej veji.

Priorities and delivery status are kept in a local planning note and are not
published. This document records a proposed contract, examples and acceptance
criteria for future implementation.

Runestra razširi obstoječo objavo GitHub PR na Azure DevOps Services in Bitbucket Cloud. Skupni postopek preverja repozitorij, objavljeni commit, lastništvo PR in rezultat ustvarjanja; adapterji izvajajo ponudnikove API klice. Obstoječa GitHub workflowa in evidenca učinkov ostaneta združljiva.

Uporabnik je potrdil prvi obseg za obe oblačni storitvi in enako osnovno ravnanje ob ponovitvi kot pri obstoječi GitHub akciji. Azure DevOps Server in Bitbucket Data Center sta poznejši razširitvi z ločenima API adapterjema in sprejemnimi testi.

## Obseg prve izdaje

Podpora pokrije zasebni repozitorij od registracije projekta in osvežitve konfiguracije do checkouta, objave veje ter ustvarjanja ali ponovne potrditve PR. Source in target sta v istem repozitoriju. PR ima eksplicitno ciljno vejo, naslov, opis in preverjen source commit. Uspešen korak vrne uporabniški URL PR in njegovo identiteto.

Samodejni merge, reviewerji, fork PR, posodabljanje že objavljenih PR na nove commite, Azure Boards, Bitbucket Issues, CI statusi in webhooki niso del prve izdaje. Obstoječi approval koraki ostanejo del workflowa; odprt PR sam po sebi ne pomeni uspešnih preverjanj CI ali pripravljenosti za merge.

Osnovna ponovitev pomeni, da korak vrne že ustvarjeni skladni PR ali ustvari novega, ko ni PR niti evidence negotovega prejšnjega poskusa. Ponovno odpiranje zaprtega PR, samodejno ponavljanje negotovega ustvarjanja, nove rezervacije za sočasne objave in koordinacija obnove med runnerji so poznejši obseg.

## Izhodišče v trenutni kodi

| Področje | Trenutno stanje | Potrebna razširitev |
|---|---|---|
| GitHub PR | `src/aorch/extensions/git.py`, `git.pr` in `github.open_pr` uporabljata `gh pr list/create/view` | Izločiti skupno usklajevanje PR; GitHub ostane adapter za obstoječi `gh` |
| Ponovitev | Marker v opisu, `EffectLedger`, preverjanje head commita, base in odprtega stanja | Enake zahteve za nova ponudnika, polna paginacija in varna obravnava negotovega izida |
| Git objava | `git.push` preverja remote ref pred objavo in po njej | Preveriti tudi ujemanje dejanskega Git remote s PR repozitorijem |
| Povezave | `ProjectConfig.connections` vsebuje samo `JiraConnection` | Dodati tipizirani povezavi za Azure DevOps in Bitbucket; ohraniti staro Jira sintakso |
| Poverilnice | `project_secret()` omogoča namenski dostop; `environment()` skrivnosti izloča iz splošnega okolja podprocesov | PR adapterji uporabijo namenski dostop do projektnih skrivnosti |
| Git avtentikacija | Refresh in checkout uporabljata `git_environment`; akcije v `extensions/git.py` uporabljajo splošni `environment(config)` | Združiti avtentikacijo za refresh, fetch, `ls-remote` in push |
| Dovoljeni hosti | Privzeto `github.com`, `gitlab.com`, `bitbucket.org` | Dodati natančne Azure hoste in ohraniti omejitve portov ter protokolov |
| Katalog akcij | Sheme, dispatch, zahteve paketa in runner capability izhajajo iz `ACTION_REGISTRY` | Nove akcije registrirati v celotni poti in jim eksplicitno nastaviti `workspace: true` |

Trenutni transportni protokol v `src/aorch/contracts/__init__.py` je `5`. Različice akcij in konfiguracije se obravnavajo ločeno od transportnega protokola.

## Predlagana arhitektura

Uvesti `review.open_pr` kot skupno akcijo. Ponudnika določa imenovana povezava, ne ugibanje iz domene. Dodati tudi `azure_devops.open_pr` in `bitbucket.open_pr` kot priročni imeni istega izvajalca, ki preverita pravilen tip povezave.

`github.open_pr` in `git.pr` ohranita trenutne vhode, izhode, različico pogodbe, marker in `external_effects.kind = git.pr`. Izločitev njunega izvajalca ne sme spremeniti ravnanja ob ponovitvi. Nova skupna akcija za Azure in Bitbucket ima svojo pogodbo; njuni priročni imeni za isti repozitorij in vejo uporabljata isti ključ učinka. Migracija GitHub workflowov na novo konfiguracijo povezav ni potrebna za prvo izdajo.

Predlagana razdelitev kode:

| Datoteka ali sklop | Odgovornost |
|---|---|
| `extensions/review.py` | Predpogoji objave, lastništvo, ledger, usklajevanje rezultata in `StepResult` |
| `extensions/review_providers/github.py` | Obstoječi klici `gh`, prevedeni v skupni interni model |
| `extensions/review_providers/azure_devops.py` | Azure REST API prek obstoječega `httpx` |
| `extensions/review_providers/bitbucket.py` | Bitbucket REST API prek `httpx` |
| `runner/git.py` | Omejena Git avtentikacija, normalizacija remote in preverjanje identitete |
| `config/models.py` | Povezave, preverjanje njihovega tipa in združljiva Jira konfiguracija |
| `extensions/schemas.py` in `extensions/__init__.py` | Katalog, vhodne in izhodne sheme, opis, dispatch ter zunanje učinke |

Adapter zagotavlja operacije za razrešitev repozitorija, seznam kandidatov PR, ustvarjanje in branje enega PR. Vrne normalizirano identiteto repozitorija, ID, uporabniški URL, source in target vejo, source commit, stanje in opis. Skupni izvajalec odloča, ali je rezultat skladen s preverjeno objavo.

Azure in Bitbucket ne potrebujeta dodatnih CLI orodij. GitHub v prvem koraku ohrani `gh`, da razširitev ne vključuje še migracije njegove avtentikacije.

## Povezave in primer uporabe

Predlagana sintaksa, ki še ni implementirana:

```yaml
connections:
  ado:
    provider: azure_devops
    organization: example-org
    project: example-project
    repository: example-repository
    authentication: pat
    token_env: AZURE_DEVOPS_TOKEN

  bb:
    provider: bitbucket
    workspace: example-workspace
    repository: example-repository
    authentication: api_token
    email: automation@example.com
    token_env: BITBUCKET_API_TOKEN
```

Repozitorij je v povezavi, zato workflow ne ponavlja Azure organizacije, projekta ali Bitbucket workspacea. Token je projektna skrivnost, YAML vsebuje samo njegovo referenco. Git poverilnice za začetni refresh morajo biti nastavljene že pred branjem konfiguracije iz zasebnega repozitorija; povezava v tem repozitoriju ne more sama rešiti začetnega dostopa.

```yaml
pull_request:
  type: action
  action: review.open_pr
  needs: [push]
  inputs:
    connection: ado
    remote: origin
    base: main
    title: Apply verified changes
    body: Changes validated by the workflow.
```

Za Bitbucket se spremeni `connection: bb`. `needs: [push]` določa vrstni red; preverjeni commit izvajalec prebere iz obstoječe evidence workspacea. Morebitne dinamične naslove in opise workflow prenese z `input_refs`.

Nova izhodna pogodba ohrani uporabna polja `pull_request`, `number`, `branch`, `commit`, `head_commit`, `base` in `external_effects`. Doda `provider` in kanonično identiteto repozitorija. `number` predstavlja lokalni numerični ID PR pri ponudniku. Novi učinki imajo `kind: review.open_pr`; stara GitHub pogodba ostane nespremenjena.

Nova konfiguracijska oblika potrebuje eksplicitno preverjanje združljivosti. Določiti izvorno različico in normalizacijo v loaderju ter preverjanje paketa. Starejši runner mora nov paket zavrniti ali biti izločen iz izbire pred izvajanjem, tudi če sam korak uporablja znano Git akcijo. Transportni protokol se spremeni le, če se spremeni oblika sporočil.

## Azure DevOps Services

Uporabiti Azure Repos Git REST API `7.1`. Organizacijo, projekt in repozitorij razrešiti v stabilna ID; imena pri gradnji URL pravilno kodirati. Veji API pošljemo kot `refs/heads/...`. Ustvarjanje uporablja `POST .../_apis/git/repositories/{repositoryId}/pullrequests?api-version=7.1`, z `title`, `description`, `sourceRefName` in `targetRefName`. [Microsoft PR API](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-requests/create?view=azure-devops-rest-7.1)

Pri iskanju zajeti vsa stanja in vse strani, pri branju ločiti `active`, `completed` in `abandoned`. Primerjati trenutno source ref in PR source commit, ne merge commita. `lastMergeSourceCommit` opisuje source ob zadnji obdelavi merge, zato mora adapter upoštevati zakasnitev API in pred uspehom potrditi pričakovani head. API URL se ne sme vrniti kot uporabniški URL PR. [Microsoft seznam PR](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-requests/get-pull-requests?view=azure-devops-rest-7.1), [Microsoft model PR](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-requests/create?view=azure-devops-rest-7.1)

Za začetno integracijo predlagam PAT, omejen na organizacijo, s pravico Code Read and Write in potrebnimi dovoljenji repozitorija za objavo veje ter PR. To je omejitev prve izdaje. Microsoft priporoča Entra tokene; za produkcijsko avtomatizacijo naj naslednji korak podpre service principal ali managed identity z obnovo kratkotrajnih tokenov. Starega Azure DevOps OAuth ne uvajamo. [Microsoft avtentikacija](https://learn.microsoft.com/en-us/azure/devops/repos/git/auth-overview?view=azure-devops), [Microsoft PAT](https://learn.microsoft.com/en-us/azure/devops/organizations/accounts/use-personal-access-tokens-to-authenticate?view=azure-devops)

V prvem obsegu podpreti `dev.azure.com` in `ssh.dev.azure.com`. Stare URL z `visualstudio.com` jasno zavrniti z navodilom za kanonični URL. Natančen seznam hostov je del konfiguracije, brez splošne wildcard izjeme.

## Bitbucket Cloud

Uporabiti REST API `2.0`, endpoint `POST /repositories/{workspace}/{repo_slug}/pullrequests`. Eksplicitno poslati `title`, `description`, `source.branch.name` in `destination.branch.name`. Preveriti `source.commit.hash`, target vejo, obe identiteti repozitorija ter stanje `OPEN`. Uporabniški URL vrniti iz `links.html.href`. Seznam kandidatov zajame vsa dokumentirana stanja in vse strani. [Bitbucket PR API](https://developer.atlassian.com/cloud/bitbucket/rest/api-group-pullrequests/)

Za prvo izdajo uporabiti scoped API token in Basic avtentikacijo z Atlassian emailom. App passwords ne uvajamo, API tokeni so njihova dolgoročna zamenjava. Poznejši repository access token ali OAuth dobi ločen ekspliciten način avtentikacije. [Bitbucket API tokeni](https://support.atlassian.com/bitbucket-cloud/docs/api-tokens/), [Uporaba API tokenov](https://support.atlassian.com/bitbucket-cloud/docs/using-api-tokens/)

Za celotno pot so potrebni `read:repository:bitbucket`, `write:repository:bitbucket`, `read:pullrequest:bitbucket` in `write:pullrequest:bitbucket`. Pravica pisanja PR ne zagotovi pravice za Git push; pravice se ne dedujejo samodejno. Git HTTPS ima svoj način podajanja uporabniškega imena, zato REST emaila ne uporabljamo slepo tudi za Git. [Bitbucket pravice tokena](https://support.atlassian.com/bitbucket-cloud/docs/api-token-permissions/), [Uporaba API tokenov](https://support.atlassian.com/bitbucket-cloud/docs/using-api-tokens/)

Git gostuje na `bitbucket.org`, API na `api.bitbucket.org`. Token dovolimo samo ustreznemu gostitelju; tudi paginacijski `next` mora prestati preverjanje izvora.

## Pravila varne objave in ponovitve

Za vse tri ponudnike velja osnovno ravnanje obstoječe GitHub akcije:

| Ugotovljeno stanje | Rezultat koraka |
|---|---|
| Ni kandidatnega PR in ni zabeleženega prejšnjega namena | Zapisati namen, ustvariti PR, ga prebrati in potrditi |
| Naš odprti PR ima pravilen commit in ciljno vejo | Vrniti isti PR brez ponovnega ustvarjanja |
| Naš PR je zaprt, merged, declined ali abandoned | `needs_attention`, brez odpiranja ali ustvarjanja nadomestnega PR |
| PR pripada drugemu postopku ali nima našega markerja | `needs_attention` |
| Obstaja zabeležen namen, vendar našega PR ni mogoče potrditi | `needs_attention`, brez novega POST |
| PR ima napačen commit ali ciljno vejo | Napaka preverjanja, brez spreminjanja PR |

Postopek izvajanja:

1. Preveriti lokalni `commit.json`, HEAD in lastništvo workspacea. Source veja mora na izbranem remote kazati na isti preverjeni commit.
2. Razrešiti kanonični repozitorij pri ponudniku in potrditi, da se ujema z Git remote. Pravilen commit v drugem repozitoriju ni dovolj.
3. Poiskati vse kandidatne PR za source vejo, tudi zaprte. Nepopolno branje, napaka pravic ali dosežena meja paginacije nikoli ne pomenijo, da PR ne obstaja.
4. Potrditi marker, repo, vejo, target, commit in odprto stanje. Tuj PR, več zadetkov, spremenjen marker ali zaprti PR zahtevajo `needs_attention`.
5. Pred POST trajno zapisati namen v obstoječi `EffectLedger`. Novi ključ vključuje ponudnika, stabilni ID repozitorija in source vejo; target in commit sta preverjani lastnosti iste objave. GitHub ohrani svoj obstoječi ključ.
6. Po ustvarjanju PR ponovno prebrati in preveriti. Marker vključiti v opis ter ga potrditi tudi po ponovnem branju. Omejitev dolžine opisa preveriti pred zapisom namena in ustvarjanjem.
7. Šele po potrditvi zapisati rezultat v ledger in vrniti `succeeded`.

Timeout, prekinitev ali izgubljen odgovor po POST pomenijo negotov izid. Ob naslednji izvedbi korak najprej poišče že ustvarjeni PR. Če ga najde in preveri, vrne njegov rezultat. Če ga ne najde, vendar obstaja zapisan namen, vrne `needs_attention`. Ta postopek ne dodaja samodejnega čakanja na pojav PR ali ponovnega POST. Po ponovnem zagonu uporablja isto ohranjeno evidenco in workspace.

Prva izdaja ohrani sedanjo omejitev lokalne evidence. Ne zagotavlja enkratnega učinka pri sočasni objavi z več runnerjev ali po izgubi njihovega diska. Marker je sled za usklajevanje, ni varnostni dokaz identitete avtorja PR.

Branja imajo omejene ponovitve za rate limit in začasne napake, s spoštovanjem `Retry-After`. `401` in `403` dobita jasno diagnostiko za poverilnice oziroma pravice; `404` ne šteje kot dokaz odsotnosti repozitorija, ker ponudnik lahko skrije zasebni vir. Preklic po poslanem POST mora ohraniti sled za poznejše usklajevanje.

## Git avtentikacija in uporabniški tok

Isti namenski Git helper mora veljati pri projektnem refreshu, checkoutu, preverjanju ref in pushu. Skrivnosti prebere samo za izbrani Git korak; splošno okolje shell in agent korakov jih ne prejme. Podpreti HTTPS in SSH, s strogim preverjanjem SSH hosta. REST credential in Git credential sta ločena namena, tudi če uporabnik za oba izbere isti token.

Vrednosti tokenov ostanejo v obstoječem sistemu projektnih skrivnosti. Ne smejo biti v YAML, Git URL, argumentih procesov, izhodih akcij ali opisih PR. Redakcija mora zajeti tudi Authorization header in njegove kodirane oblike. Obstoječi sistem ni splošna izolacija pred poljubno kodo na runnerju; razširitev ne uvaja takšne trditve.

V UI uporabnik doda Git source, nastavi projektne Git skrivnosti in dodeli runner. Nato nastavi povezavo PR in njeno skrivnost. Diagnostika posebej pokaže dostop do repozitorija in dostop do PR API. Preverjanje povezave je samo bralno, uspešen bralni test ne dokazuje pravice pisanja. Rezultat runa pokaže ponudnika, povezavo do PR, source vejo, target in preverjeni commit.

Nova oblika povezav potrebuje tudi prikaz napak v obstoječem urejanju konfiguracije. Ločen čarovnik za prijavo v Microsoft ali Atlassian ni potreben za prvo izdajo.

## Preverjanje in sprejem

Lokalni testi pokrijejo stare GitHub pogodbe, novo konfiguracijo, katalog, dispatch, izbiro runnerja, vrnitev istega PR ob ponovitvi ter napačen repo, target, commit in stanje. Preveriti tudi tuji PR, odstranjen marker, kandidat na drugi strani seznama, zunanji `next` URL, timeout po POST, ponovni zagon z ohranjeno evidenco, preklic in redakcijo skrivnosti.

Pri vsakem ponudniku preveriti oba izida izgubljenega odgovora: oddaljeni PR obstaja in naslednja izvedba ga vrne; oddaljenega PR ni mogoče potrditi in naslednja izvedba zaradi zapisanega namena vrne `needs_attention` brez novega ustvarjanja. Zaprt PR se ne odpre ponovno. Testi novih rezervacij, koordinacije med runnerji in obnove po izgubi diska spadajo v poznejšo razširitev.

Integracijski test celotne Git poti uporabi lokalni bare remote in simuliran ponudnikov API. Pri Azure mora posebej simulirati zakasnjeno polje source commita. Stari Jira primeri in modularni V3 paketi ostanejo veljavni. Preverjanje GitHub regresije razširi obstoječi test `test_executor_runs_pinned_checkout_branch_verify_commit_push_and_recovers_pr` v `tests/test_extensions.py`.

Po implementaciji izvesti `uv run --locked --group dev python -m pytest -q` in projektne statične preglede. Za zunanji sprejem sta potrebna dovoljena testna repozitorija, ločeni poverilnici in dejanski workflow skozi engine ter runner. Na obeh ponudnikih potrditi en sam odprt PR, pravilni target in head commit ter ponovno izvedbo brez dodatnega PR.

## Poznejše razširitve

Po sprejemu prve izdaje dodati Entra avtentikacijo za Azure in obnovo tokenov. Nato po potrebah repository access tokene oziroma OAuth za Bitbucket, reviewerje, draft način in kontrolirano posodabljanje PR. Naprednejše usklajevanje negotovega izida, atomarne rezervacije in koordinacija med runnerji so ločen obseg z lastnimi testi. Server in Data Center zahtevata svoje API adapterje, nastavljive gostitelje, podporne različice in realna testna okolja. Skupna pravila objave ostanejo uporabna tudi za ti različici.
