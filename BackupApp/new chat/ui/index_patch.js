
// PATCH: hide table when item selected

function selectMarketItem(itemId) {
  const selected = latestExplorerItems.find(item => Number(item.id) === Number(itemId));
  latestSelectedItemId = itemId;
  latestSelectedItem = selected || null;

  if (selected) saveRecentMarketSearch(selected);

  // ✅ HIDE TABLE WHEN SELECTED
  const resultsCard = document.querySelector(".market-results-panel");
  if (resultsCard) resultsCard.style.display = "none";

  setExplorerSelectionState(true);
  renderMarketDetail(selected);

  const activeRange = document.querySelector(".range-btn.active")?.dataset.range || "month";

  fetch(`/market/explorer/history/${itemId}?range=${activeRange}`)
    .then(r => r.json())
    .then(data => {
      const normalized = normalizeHistoryPoints(data.points || [], activeRange);
      chartPanelSubtitleEl.textContent = `${selected?.name || 'Selected item'} • ${String(activeRange).toUpperCase()} view • ${normalized.length} plotted dots`;
      drawHistoryChart(normalized, activeRange, selected);
    })
    .catch(err => console.error(err));
}


// PATCH: show table again on new search

async function searchMarketExplorer() {
  marketSearchBtn.disabled = true;

  // ✅ SHOW TABLE AGAIN ON SEARCH
  const resultsCard = document.querySelector(".market-results-panel");
  if (resultsCard) resultsCard.style.display = "block";

  marketMetaEl.textContent = "Searching cache...";

  try {
    const rawQuery = marketSearchInputEl.value || "";
    const query = encodeURIComponent(rawQuery);

    const data = await fetchJsonWithTimeout(`/market/explorer/search?q=${query}&limit=100`);

    latestExplorerItems = data.items || [];

    const freshness = data?.freshness_text || (data?.is_stale ? "Stale" : "Fresh");
    const snapshotText = data?.snapshot_bucket || data?.updated_at || "-";

    marketMetaEl.textContent =
      `Cache snapshot: ${snapshotText} • ${freshness} • ${latestExplorerItems.length} items`;

    renderMarketExplorerTable(latestExplorerItems);
    clearMarketSelection();

  } catch (error) {
    marketMetaEl.textContent = "Search failed";
  } finally {
    marketSearchBtn.disabled = false;
  }
}
