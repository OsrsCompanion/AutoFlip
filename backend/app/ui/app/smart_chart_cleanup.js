
document.addEventListener("DOMContentLoaded", function () {
  try {
    const panels = document.querySelectorAll(
      '[class*="useful"], [class*="spread-read"], [class*="spike-read"], [class*="best-use"]'
    );
    let texts = [];
    panels.forEach(p => {
      if (p && p.innerText) {
        texts.push(p.innerText.trim());
        p.style.display = "none";
      }
    });

    if (!texts.length) return;

    const smartLine = document.createElement("div");
    smartLine.style.margin = "12px 0";
    smartLine.style.padding = "10px 14px";
    smartLine.style.border = "1px solid rgba(200,160,80,0.2)";
    smartLine.style.borderRadius = "10px";
    smartLine.style.background = "rgba(20,12,8,0.4)";
    smartLine.style.fontSize = "14px";
    smartLine.innerText = "Smart Read: " + texts.join(" • ");

    const chart = document.querySelector("#marketChartShell");
    if (chart) chart.parentNode.insertBefore(smartLine, chart);
  } catch (e) {
    console.error("Smart cleanup failed", e);
  }
});
