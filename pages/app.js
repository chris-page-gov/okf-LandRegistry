"use strict";

const PAGE_SIZE = 24;
const FILTERS = [
  { key: "content_type", param: "filter.content_type", values: (r) => [r.record_type] },
  { key: "service", param: "filter.service", values: (r) => [r.source_family] },
  { key: "audience", param: "filter.audience", values: (r) => r.audience || [] },
  { key: "access", param: "filter.access", values: (r) => [r.access_model] },
  { key: "format", param: "filter.format", values: (r) => r.formats || [] },
  { key: "geography", param: "filter.geography", values: (r) => [r.jurisdiction] },
  { key: "licence", param: "filter.licence", values: (r) => [r.licence] },
  { key: "language", param: "filter.language", values: (r) => r.languages || [] },
  { key: "update_frequency", param: "filter.update_frequency", values: (r) => [r.cadence] },
  { key: "topic", param: "filter.topic", values: (r) => r.topics || [] },
];

const state = {
  records: [],
  filtered: [],
  shown: PAGE_SIZE,
  shardCache: new Map(),
  renderGeneration: 0,
  searchContract: {
    token_pattern: "[a-z0-9]+",
    token_min_length: 2,
    stopwords: new Set(),
    heading_fields: ["title", "record_type", "topics"],
    body_fields: [
      "title", "description", "publisher", "record_type", "source_family",
      "jurisdiction", "access_model", "authentication", "licence", "cadence",
      "audience", "formats", "topics", "languages", "caveats", "source_urls",
    ],
    weights: { heading: 8, body: 2, reviewed_curation_bonus: 3 },
    minimum_should_match: {
      apply_from_query_tokens: 3,
      minimum_matches: 2,
      ratio_numerator: 3,
      ratio_denominator: 10,
    },
  },
};

const elements = {
  form: document.querySelector("#search-form"),
  search: document.querySelector("#search"),
  sort: document.querySelector("#sort"),
  clear: document.querySelector("#clear-filters"),
  results: document.querySelector("#results"),
  status: document.querySelector("#result-status"),
  loadMore: document.querySelector("#load-more"),
  statRecords: document.querySelector("#stat-records"),
  statSources: document.querySelector("#stat-sources"),
};

function normalize(value) {
  return String(value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function fieldsText(record, fields) {
  return fields
    .flatMap((field) => {
      const value = record[field];
      return Array.isArray(value) ? value : [value || ""];
    })
    .join(" ");
}

function textFor(record) {
  return normalize(fieldsText(record, state.searchContract.body_fields));
}

function tokens(value) {
  const pattern = new RegExp(state.searchContract.token_pattern, "g");
  const found = normalize(value).match(pattern) || [];
  return new Set(found.filter(
    (token) => token.length >= state.searchContract.token_min_length
      && !state.searchContract.stopwords.has(token),
  ));
}

function create(tag, options = {}) {
  const node = document.createElement(tag);
  if (options.className) {
    node.className = options.className;
  }
  if (options.text !== undefined) {
    node.textContent = options.text;
  }
  if (options.attributes) {
    Object.entries(options.attributes).forEach(([name, value]) => {
      node.setAttribute(name, value);
    });
  }
  return node;
}

function appendText(parent, tag, text, className) {
  const child = create(tag, { text, className });
  parent.append(child);
  return child;
}

function selectedFilters() {
  const selected = new Map();
  FILTERS.forEach((filter) => {
    const control = document.querySelector(`[data-filter="${filter.key}"]`);
    if (control && control.value) {
      selected.set(filter.key, control.value);
    }
  });
  return selected;
}

function relevanceScore(record, terms) {
  if (!terms.length) {
    return 0;
  }
  const headingTerms = record._headingTokenSet
    || (typeof record.heading_tokens === "string"
      ? new Set(record.heading_tokens.split(" ").filter(Boolean))
      : tokens(fieldsText(record, state.searchContract.heading_fields)));
  const bodyTerms = record._bodyTokenSet
    || (typeof record.body_tokens === "string"
      ? new Set(record.body_tokens.split(" ").filter(Boolean))
      : tokens(textFor(record)));
  const minimum = state.searchContract.minimum_should_match;
  const requiredMatches = terms.length >= minimum.apply_from_query_tokens
    ? Math.max(
      minimum.minimum_matches,
      Math.floor(
        (
          terms.length * minimum.ratio_numerator
          + minimum.ratio_denominator
          - 1
        ) / minimum.ratio_denominator,
      ),
    )
    : 1;
  const matchedTerms = terms.filter(
    (term) => headingTerms.has(term) || bodyTerms.has(term),
  );
  if (matchedTerms.length < requiredMatches) {
    return 0;
  }
  let score = terms.reduce((total, term) => {
    if (headingTerms.has(term)) {
      return total + state.searchContract.weights.heading;
    }
    if (bodyTerms.has(term)) {
      return total + state.searchContract.weights.body;
    }
    return total;
  }, 0);
  if (score && record.curation === "reviewed") {
    score += state.searchContract.weights.reviewed_curation_bonus;
  }
  return score;
}

function applyFilters(options = {}) {
  const shouldWriteUrl = options.writeUrl !== false;
  const historyMode = options.historyMode || "replace";
  const query = elements.search.value.trim();
  const terms = [...tokens(query)];
  const filters = selectedFilters();

  state.filtered = state.records.filter((record) => {
    if (terms.length && relevanceScore(record, terms) === 0) {
      return false;
    }
    return FILTERS.every((filter) => {
      const chosen = filters.get(filter.key);
      if (!chosen) {
        return true;
      }
      return filter.values(record).some((value) => String(value) === chosen);
    });
  });

  const sort = elements.sort.value;
  state.filtered.sort((left, right) => {
    if (sort === "title") {
      return left.title.localeCompare(right.title, "en-GB", { sensitivity: "base" })
        || left.id.localeCompare(right.id);
    }
    if (sort === "latest") {
      const leftDate = Date.parse(left.publisher_last_updated || "") || 0;
      const rightDate = Date.parse(right.publisher_last_updated || "") || 0;
      return rightDate - leftDate
        || left.title.localeCompare(right.title, "en-GB")
        || left.id.localeCompare(right.id);
    }
    const scoreDifference = relevanceScore(right, terms) - relevanceScore(left, terms);
    return scoreDifference
      || left.title.localeCompare(right.title, "en-GB", { sensitivity: "base" })
      || left.id.localeCompare(right.id);
  });

  if (options.resetPage !== false) {
    state.shown = PAGE_SIZE;
  }
  if (shouldWriteUrl) {
    writeUrlState(historyMode);
  }
  render().catch(showLoadError);
}

function writeUrlState(historyMode) {
  const params = new URLSearchParams();
  if (elements.search.value.trim()) {
    params.set("q", elements.search.value.trim());
  }
  FILTERS.forEach((filter) => {
    const control = document.querySelector(`[data-filter="${filter.key}"]`);
    if (control && control.value) {
      params.set(filter.param, control.value);
    }
  });
  if (elements.sort.value !== "relevance") {
    params.set("sort", elements.sort.value);
  }
  const query = params.toString();
  const next = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  if (historyMode === "push") {
    window.history.pushState({ okfFilters: true }, "", next);
  } else {
    window.history.replaceState({ okfFilters: true }, "", next);
  }
}

function restoreUrlState() {
  const params = new URLSearchParams(window.location.search);
  elements.search.value = params.get("q") || "";
  const sort = params.get("sort") || "relevance";
  elements.sort.value = ["relevance", "title", "latest"].includes(sort)
    ? sort
    : "relevance";
  FILTERS.forEach((filter) => {
    const control = document.querySelector(`[data-filter="${filter.key}"]`);
    if (!control) {
      return;
    }
    const value = params.get(filter.param) || "";
    control.value = [...control.options].some((option) => option.value === value)
      ? value
      : "";
  });
}

function facetOptions(filter) {
  const counts = new Map();
  state.records.forEach((record) => {
    filter.values(record).forEach((rawValue) => {
      const value = String(rawValue || "").trim();
      if (value) {
        counts.set(value, (counts.get(value) || 0) + 1);
      }
    });
  });
  return [...counts.entries()].sort(([left], [right]) =>
    left.localeCompare(right, "en-GB", { sensitivity: "base" }),
  );
}

function populateFilters() {
  FILTERS.forEach((filter) => {
    const control = document.querySelector(`[data-filter="${filter.key}"]`);
    if (!control) {
      return;
    }
    facetOptions(filter).forEach(([value, count]) => {
      const option = create("option", {
        text: `${value} (${count.toLocaleString("en-GB")})`,
        attributes: { value },
      });
      control.append(option);
    });
  });
}

function badge(text) {
  return create("span", { className: "badge", text });
}

function metadataRow(term, description) {
  const wrapper = create("div");
  appendText(wrapper, "dt", term);
  appendText(wrapper, "dd", description || "Not stated");
  return wrapper;
}

function normalizedRoute(value) {
  return String(value || "").replace(/\/+$/, "");
}

function routeText(value) {
  try {
    const route = new URL(value);
    return `${route.hostname}${route.pathname}${route.search}`;
  } catch {
    return value;
  }
}

function recordCard(record) {
  const article = create("article", {
    className: "result-card",
    attributes: { "data-authority": record.authority_tier || "unassessed" },
  });
  const badges = create("div", { className: "badge-row" });
  badges.append(
    badge(`Authority ${record.authority_tier || "unassessed"}`),
    badge(record.record_type),
  );
  article.append(badges);

  const heading = create("h3");
  const link = create("a", {
    text: record.title,
    attributes: {
      href: record.url,
      "aria-label": `${record.title} — open source record`,
    },
  });
  heading.append(link);
  article.append(heading);
  appendText(article, "p", record.description || "No source summary was supplied.");

  const metadata = create("dl", { className: "record-meta" });
  metadata.append(
    metadataRow("Authority", record.authority_role),
    metadataRow("Source family", record.source_family),
    metadataRow("Access state", record.access_state),
    metadataRow("Access route", record.access_model),
    metadataRow(
      "Rights",
      `${record.rights_state || "unknown"} (${record.rights_ref || "unassessed"})`,
    ),
    metadataRow("Licence summary", record.licence),
    metadataRow("Cadence", record.cadence),
    metadataRow("Geography", record.jurisdiction),
    metadataRow(
      "Languages",
      record.languages && record.languages.length
        ? record.languages.join(", ")
        : "Not stated by source metadata",
    ),
    metadataRow("Observed", record.observed_at),
  );
  article.append(metadata);

  if (record.source_urls && record.source_urls.length) {
    const routes = create("div", { className: "source-routes" });
    appendText(routes, "strong", "Governed source and evidence routes");
    const list = create("ul");
    record.source_urls.forEach((route, index) => {
      const item = create("li");
      const primary = normalizedRoute(route) === normalizedRoute(record.url);
      appendText(
        item,
        "span",
        primary ? "Primary record: " : `Supporting source ${index + 1}: `,
        "source-route-label",
      );
      item.append(create("a", {
        text: routeText(route),
        attributes: {
          href: route,
          "aria-label": `${primary ? "Primary record" : "Supporting source"} — ${route}`,
        },
      }));
      list.append(item);
    });
    routes.append(list);
    article.append(routes);
  }

  if (record.caveats && record.caveats.length) {
    const keyCaveat = create("div", { className: "key-caveat" });
    appendText(
      keyCaveat,
      "strong",
      record.caveats.length === 1 ? "Key caveat" : "Key caveats",
    );
    const visibleCaveats = record.caveats.slice(0, 2);
    if (visibleCaveats.length === 1) {
      appendText(keyCaveat, "p", visibleCaveats[0]);
    } else {
      const keyList = create("ul");
      visibleCaveats.forEach((item) => appendText(keyList, "li", item));
      keyCaveat.append(keyList);
    }
    article.append(keyCaveat);
    if (record.caveats.length > visibleCaveats.length) {
      const details = create("details", { className: "caveat" });
      appendText(
        details,
        "summary",
        `Read ${record.caveats.length - visibleCaveats.length} more caveat${record.caveats.length - visibleCaveats.length === 1 ? "" : "s"}`,
      );
      const list = create("ul");
      record.caveats
        .slice(visibleCaveats.length)
        .forEach((item) => appendText(list, "li", item));
      details.append(list);
      article.append(details);
    }
  }
  return article;
}

async function loadShard(shard) {
  if (!Number.isInteger(shard) || shard < 0) {
    throw new Error(`Search index contains invalid shard ${shard}`);
  }
  if (!state.shardCache.has(shard)) {
    const path = `./data/records/records-${String(shard).padStart(3, "0")}.json`;
    const promise = fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
    }).then(async (response) => {
      if (!response.ok) {
        throw new Error(`Record shard ${shard} returned HTTP ${response.status}`);
      }
      const payload = await response.json();
      if (
        payload.schema !== "okf-hmlr-record-shard.v1"
        || payload.shard !== shard
        || !Array.isArray(payload.records)
        || payload.record_count !== payload.records.length
      ) {
        throw new Error(`Record shard ${shard} is invalid`);
      }
      return new Map(payload.records.map((record) => [record.id, record]));
    });
    state.shardCache.set(shard, promise);
  }
  return state.shardCache.get(shard);
}

async function hydrateRecords(records) {
  const shardIds = [...new Set(records.map((record) => record.shard))];
  const shardMaps = new Map(
    await Promise.all(
      shardIds.map(async (shard) => [shard, await loadShard(shard)]),
    ),
  );
  return records.map((record) => {
    const full = shardMaps.get(record.shard)?.get(record.id);
    if (!full) {
      throw new Error(`Record ${record.id} is absent from shard ${record.shard}`);
    }
    return full;
  });
}

async function render() {
  const generation = ++state.renderGeneration;
  const count = state.filtered.length;
  const visible = Math.min(state.shown, count);
  elements.results.setAttribute("aria-busy", "true");
  elements.status.textContent = count
    ? `Loading ${visible.toLocaleString("en-GB")} matching records…`
    : "No matching records";
  const compactVisible = state.filtered.slice(0, state.shown);
  const fullVisible = compactVisible.length
    ? await hydrateRecords(compactVisible)
    : [];
  if (generation !== state.renderGeneration) {
    return;
  }
  elements.results.replaceChildren();
  elements.results.setAttribute("aria-busy", "false");

  if (!count) {
    const empty = create("div", { className: "empty-state" });
    appendText(empty, "h3", "No records match this view");
    appendText(
      empty,
      "p",
      "Try fewer terms, clear a filter or use the official HMLR organisation page for live discovery.",
    );
    elements.results.append(empty);
  } else {
    fullVisible.forEach((record) => {
      elements.results.append(recordCard(record));
    });
  }

  elements.status.textContent = count
    ? `Showing ${visible.toLocaleString("en-GB")} of ${count.toLocaleString("en-GB")} matching records`
    : "No matching records";
  elements.loadMore.hidden = visible >= count;
  if (!elements.loadMore.hidden) {
    elements.loadMore.textContent = `Show ${Math.min(PAGE_SIZE, count - visible)} more results`;
  }
}

function clearFilters() {
  elements.search.value = "";
  elements.sort.value = "relevance";
  FILTERS.forEach((filter) => {
    const control = document.querySelector(`[data-filter="${filter.key}"]`);
    if (control) {
      control.value = "";
    }
  });
  applyFilters({ historyMode: "push" });
  elements.search.focus();
}

function showLoadError(error) {
  elements.results.replaceChildren();
  elements.results.setAttribute("aria-busy", "false");
  const panel = create("div", { className: "error-state" });
  appendText(panel, "h3", "The static catalogue could not be loaded");
  appendText(
    panel,
    "p",
    "The JSON and CSV downloads may still be opened directly. No external service was called.",
  );
  const detail = create("details");
  appendText(detail, "summary", "Technical detail");
  appendText(detail, "p", error instanceof Error ? error.message : String(error));
  panel.append(detail);
  elements.results.append(panel);
  elements.status.textContent = "Catalogue unavailable";
}

function isSupportedSearchContract(contract) {
  if (
    contract?.schema !== "okf-hmlr-search-contract.v1"
    || contract.token_pattern !== "[a-z0-9]+"
    || contract.token_min_length !== 2
    || !Array.isArray(contract.stopwords)
    || contract.stopwords.length > 256
    || !contract.weights
  ) {
    return false;
  }
  if (!contract.stopwords.every((token, index) =>
    typeof token === "string"
    && token.length > 0
    && token.length <= 32
    && /^[a-z0-9]+$/.test(token)
    && token === token.toLowerCase()
    && (index === 0 || contract.stopwords[index - 1] < token))) {
    return false;
  }
  const minimum = contract.minimum_should_match;
  return minimum
    && Object.keys(minimum).sort().join(",")
      === "apply_from_query_tokens,minimum_matches,ratio_denominator,ratio_numerator"
    && minimum.apply_from_query_tokens === 3
    && minimum.minimum_matches === 2
    && minimum.ratio_numerator === 3
    && minimum.ratio_denominator === 10;
}

async function initialise() {
  try {
    const requestOptions = { credentials: "same-origin", cache: "no-store" };
    const [response, contractResponse] = await Promise.all([
      fetch("./data/search/index.json", requestOptions),
      fetch("./search-contract.json", requestOptions),
    ]);
    if (!response.ok || !contractResponse.ok) {
      throw new Error(
        `Static data request returned HTTP ${response.status}/${contractResponse.status}`,
      );
    }
    const payload = await response.json();
    const contract = await contractResponse.json();
    if (
      payload?.schema !== "okf-hmlr-search-index.v1"
      || !Array.isArray(payload.records)
      || payload.record_count !== payload.records.length
    ) {
      throw new Error("Compact search index is missing or unsupported");
    }
    if (
      !payload.records.every((record) =>
        record
        && typeof record.id === "string"
        && typeof record.title === "string"
        && typeof record.url === "string"
        && Array.isArray(record.source_urls)
        && Number.isInteger(record.shard)
        && typeof record.heading_tokens === "string"
        && typeof record.body_tokens === "string")
    ) {
      throw new Error("Compact search index contains an invalid record");
    }
    if (!isSupportedSearchContract(contract)) {
      throw new Error("Search contract is missing or unsupported");
    }
    state.searchContract = {
      ...contract,
      stopwords: new Set(contract.stopwords),
    };
    state.records = payload.records.map((record) => ({
      ...record,
      _headingTokenSet: new Set(record.heading_tokens.split(" ").filter(Boolean)),
      _bodyTokenSet: new Set(record.body_tokens.split(" ").filter(Boolean)),
    }));
    populateFilters();
    restoreUrlState();
    elements.statRecords.textContent = state.records.length.toLocaleString("en-GB");
    elements.statSources.textContent = new Set(
      state.records.map((record) => record.source_family),
    ).size.toLocaleString("en-GB");
    applyFilters({ writeUrl: false });
  } catch (error) {
    showLoadError(error);
  }
}

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  applyFilters({ historyMode: "push" });
});
elements.search.addEventListener("input", () => applyFilters({ writeUrl: false }));
elements.sort.addEventListener("change", () => applyFilters({ historyMode: "push" }));
FILTERS.forEach((filter) => {
  const control = document.querySelector(`[data-filter="${filter.key}"]`);
  if (control) {
    control.addEventListener("change", () => applyFilters({ historyMode: "push" }));
  }
});
elements.clear.addEventListener("click", clearFilters);
elements.loadMore.addEventListener("click", async () => {
  state.shown += PAGE_SIZE;
  try {
    await render();
  } catch (error) {
    showLoadError(error);
    return;
  }
  const firstNewCard = elements.results.children[state.shown - PAGE_SIZE];
  if (firstNewCard) {
    firstNewCard.setAttribute("tabindex", "-1");
    firstNewCard.focus();
  }
});
window.addEventListener("popstate", () => {
  restoreUrlState();
  applyFilters({ writeUrl: false });
});

initialise();
