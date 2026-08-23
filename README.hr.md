# BEMT — Benji Eve Market Tool

*[English version of this document →](README.md)*

Držiš market popunjenim. Stvari se prodaju. BEMT ti točno kaže što treba
ponovno kupiti, i to kao listu koju zalijepiš ravno u multibuy prozor u igri.

Preko službenog EVE API-ja čita tvoje vlastite sell naloge, uspoređuje ih sa
zalihama koje želiš držati i da ti razliku. Ništa više. Nikad ne postavlja
naloge, ne troši ISK i ni na koji način ne dira tvoj račun — samo čita.

---

## Instalacija

1. **Instaliraj Python** (samo ako ga već nemaš): <https://www.python.org/downloads/>
   Tijekom instalacije **označi "Add python.exe to PATH"**. To je važno.
2. **Raspakiraj BEMT mapu** gdje god želiš.
3. **Dvoklik na `run.bat`.**

Prvo pokretanje traje minutu dok se sve posloži. Nakon toga se pokreće u par
sekundi. Preglednik se otvara sam na <http://localhost:8425>.

Ostavi crni konzolni prozor otvoren dok koristiš BEMT — ako ga zatvoriš, alat
se gasi. Sve radi na tvom računalu; ništa se nigdje ne šalje.

---

## Prvi put

1. Gore desno klikni **Prijavi se preko EVE-a** i odobri svog market lika.
   Otvorit će se CCP-ova vlastita stranica za prijavu — BEMT nikad ne vidi tvoju
   lozinku.
2. Ako imaš sell naloge na više mjesta, pitat će te **koje tržište popunjavaš**.
   Odaberi ga.
3. Klikni **Osvježi**.

Taj uvoz je cijela poanta alata: svaki predmet za koji trenutno imaš sell nalog
pokupi se automatski, a **cilj** mu se postavi na veličinu naloga koji si
postavio. Ako si izlistao 100 komada Damage Control II, cilj postaje 100. Ne
moraš ništa tipkati.

---

## Svaki sljedeći put

**Osvježi** → **Kopiraj multibuy** → u EVE-u otvori multibuy prozor
(Market → Multibuy) i pritisni **Ctrl+V** → kupi.

To je cijeli krug.

---

## Kako čitati listu

| Stupac | Što znači |
|---|---|
| **Cilj** | Koliko ih želiš imati na tržištu kad je polica puna. Klikni da promijeniš. |
| **Na tržištu** | Koliko ih je trenutno još u prodaji. |
| **Hangar** | Koliko ih već imaš u hangaru na toj stanici. |
| **Kupi** | Cilj − Na tržištu − Hangar. To je ono što ide na multibuy listu. |

Obojana točkica s lijeve strane odmah pokazuje stanje:

- 🔴 **crveno** — rasprodano, ničega više nema na tržištu
- 🟡 **žuto** — pri kraju, dijelom prodano
- 🟢 **zeleno** — puna zaliha, nema se što raditi
- ⚪ **sivo** — pauzirano

Rasprodani predmeti idu na vrh liste, jer te upravo oni koštaju prodaje.

**Zašto ciljevi, a ne "što se prodalo od zadnjeg puta":** cilj govori kako želiš
da polica izgleda, pa se ne može pokvariti. Preskoči tjedan, koristi drugo
računalo, zaboravi provjeriti — odgovor je i dalje točan, jer se računa iz onoga
što je na tržištu **sada**.

---

## Dodavanje i uklanjanje predmeta

- **Dodaj:** upiši ime u polje na vrhu i pritisni Enter. Predlaže imena koja već
  poznaje; za nešto novo kopiraj točno ime iz igre (desni klik na predmet →
  Copy). Cilj možeš zadati odmah.
- **Pauziraj** predmet da ostane na listi, ali izvan liste za kupnju — zgodno za
  nešto što trenutno ne nadopunjuješ.
- **✕** ga miče u potpunosti. Ako i dalje imaš sell nalog za njega, sljedeće
  osvježavanje će ga ponovno uvesti, što je obično upravo ono što želiš.

Tvoji ciljevi su tvoji. Uvoz samo *dodaje* nove predmete — nikad ne prepisuje
broj koji si sam upisao.

---

## Postavke

- **Oduzmi zalihu koja već stoji u hangaru** — uključeno po defaultu. Ono što
  već imaš ne treba ponovno kupovati. Isključi ako želiš planirati isključivo
  prema tržišnim nalozima.
- **Automatski prati nove predmete iz mojih prodajnih naloga** — uključeno po
  defaultu. To je automatski uvoz.
- **Zaokruži količine za kupnju na višekratnik od** — stavi npr. 100 ako voliš
  nadopunjavati u okruglim serijama. 0 znači bez zaokruživanja.
- **Promijeni tržište** — ako preseliš operaciju drugdje.
- **EN / HR** — gumb gore desno prebacuje cijelo sučelje između engleskog i
  hrvatskog.

---

## Ako nešto ne radi

**"Prijavi se svojim EVE likom"** — prijava je istekla ili nikad nije napravljena.
Klikni link i prijavi se ponovno.

**"Ova verzija traži dodatna EVE dopuštenja"** — novija verzija BEMT-a traži
jedno dopuštenje više. Prijavi se još jednom i riješeno je.

**Preglednik se ne otvara ili se stranica ne učitava** — otvori
<http://localhost:8425> ručno. Ako piše da je port zauzet, BEMT je vjerojatno
već pokrenut u drugom prozoru.

**"Python was not found"** — instaliraj Python i pazi da si označio *Add
python.exe to PATH*, pa ponovno pokreni `run.bat`.

**Brojevi izgledaju kao da kasne jedno osvježavanje** — pritisni Osvježi. BEMT
gleda u EVE samo kad mu ti kažeš.

**Kreni ispočetka** — obriši mapu `data` unutar BEMT mape. To briše tvoje
ciljeve i povijest, a BEMT kreće čist kod sljedećeg pokretanja.

---

## Je li ovo sigurno?

Jest, i evo točno zašto:

- Prijavljuješ se na **CCP-ovoj vlastitoj stranici**. BEMT nikad ne vidi tvoju
  lozinku.
- Traži tri dopuštenja, sva samo za čitanje: tvoje tržišne naloge, tvoju imovinu
  i ime stanice na kojoj trguješ. **Ovdje nema nijednog dopuštenja koje može
  bilo što kupiti, prodati, pomaknuti ili potrošiti.**
- Token za prijavu čuva se isključivo na tvom računalu, u `data/bemt.db`. Ništa
  ne napušta tvoj stroj osim zahtjeva prema EVE-ovom API-ju.
- Pristup možeš opozvati kad god želiš na
  <https://community.eveonline.com/support/third-party-applications/>.

---

## Za onoga tko je ovo postavio

EVE aplikacija je registrirana s callbackom `http://localhost:8425/callback` i
ova tri scopea:

```
esi-markets.read_character_orders.v1
esi-assets.read_assets.v1
esi-universe.read_structures.v1
```

Client id je upisan u `bemt/config.py`. To je sigurno: prijava koristi PKCE, pa
nema client secreta koji bi mogao procuriti, a id samo identificira aplikaciju i
sam po sebi ne daje nikakva prava. Port je fiksiran na 8425 jer je callback URL
registriran na njega — promjena porta razbija prijavu.

Razvojne naredbe:

```bash
.venv\Scripts\python -m bemt                    # pokretanje
.venv\Scripts\pip install -e ".[dev]"           # razvojne ovisnosti
.venv\Scripts\python -m pytest tests/ -q        # testovi
```

Računica živi u `bemt/model.py` i čista je — bez I/O, potpuno pokrivena
testovima. `bemt/service.py` je I/O ljuska oko nje. Svako osvježavanje zapisuje
snimku (ukupne brojke i pojedinačne stavke) u `snapshots`/`snapshot_lines`, jer
se povijest ne može naknadno rekonstruirati.
