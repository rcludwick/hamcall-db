# hamcall-db

Openly-licensed amateur radio reference data, rebuilt from upstream sources and
published as files you can fetch — not APIs you have to register for.

Two datasets, deliberately kept apart because they carry different licences:

<div class="grid cards" markdown>

-   :material-radio-tower: **[Reflector directory](reflectors/index.md)**

    Digital-voice reflectors for D-Star, M17, YSF, NXDN, P25 and URF, served as
    **static JSON**. No API key, no account, no rate limit.

    **CC BY 4.0** — commercial use permitted.

-   :material-card-account-details: **[Callsign dataset](callsigns.md)**

    Normalized licensee records from national regulators, plus POTA parks and
    SOTA summits, published as Parquet and SQLite through GitHub Releases.

    **CC BY-NC 4.0** — non-commercial.

</div>

## The reflector API at a glance

```bash
curl -s https://rcludwick.github.io/hamcall-db/api/v1/reflectors.json
```

<div class="scroll" markdown>
<table id="networks">
  <thead>
    <tr><th>Network</th><th>Endpoint</th><th style="text-align:right">Reflectors</th><th>Updated</th></tr>
  </thead>
  <tbody id="network-rows">
    <tr><td colspan="4">Loading from <code>api/v1/index.json</code>…</td></tr>
  </tbody>
</table>
</div>

Full documentation: **[Reflector directory](reflectors/index.md)** for what it is
and how to use it, **[API reference](reflectors/api.md)** for the contract.

## Why files instead of an API

Reflector directories are scattered across upstreams with different terms,
uptime and address formats. A client that wants a "pick a reflector" list should
not have to speak six of them, hold an API token, or re-derive per-network
naming rules.

hamcall-db does that once, on a schedule, and publishes the result. What you
fetch is a file on a CDN — which means it works offline once cached, cannot rate-limit
you, and needs no credential to read.

## What is not here

* **No street addresses.** Ever, in any published artifact. They are used at
  build time for geocoding and then discarded.
* **Person grids are truncated to 4 characters.** Enough to place someone in a
  region, not enough to place them at a house.
* **No email addresses in the reflector directory.** Sysops write them into
  their own reflector blurbs upstream; republishing those as bulk machine-readable
  JSON would turn them into a harvestable list, so they are removed. See
  [Licensing](about/licensing.md#modifications).

<script>
// Fill the network table from the live manifest, so the numbers on this page
// can never disagree with the files they describe.
(function () {
  var body = document.getElementById("network-rows");
  if (!body) return;
  var LABELS = { dstar: "D-Star", m17: "M17", ysf: "YSF (Fusion)", nxdn: "NXDN", p25: "P25", urf: "URF" };
  fetch("api/v1/index.json", { cache: "no-cache" })
    .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(function (m) {
      var names = Object.keys(m.networks || {}).sort();
      if (!names.length) throw new Error("no networks in manifest");
      body.innerHTML = names.map(function (n) {
        var net = m.networks[n], href = "api/v1/" + net.url;
        return "<tr><td>" + (LABELS[n] || n) + "</td>"
          + '<td><a href="' + href + '"><code>' + href + "</code></a></td>"
          + '<td style="text-align:right">' + Number(net.count).toLocaleString() + "</td>"
          + "<td>" + net.generated + "</td></tr>";
      }).join("");
    })
    .catch(function (e) {
      body.innerHTML = '<tr><td colspan="4">Could not load '
        + '<a href="api/v1/index.json"><code>api/v1/index.json</code></a> ('
        + String(e.message || e) + ").</td></tr>";
    });
})();
</script>
