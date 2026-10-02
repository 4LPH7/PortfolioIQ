/**
 * PortfolioIQ — Universal CSV Exporter (frontend/js/export.js)
 * 
 * Provides:
 * - Robust CSV generation with double-quote escaping and UTF-8 BOM support (for Excel compatibility).
 * - exportDataToCSV(filename, headers, rows)
 * - exportTableToCSV(tableSelectorOrEl, filename)
 * - Auto-binding to any button with data-export-table="table-id"
 */

(function () {
  "use strict";

  /**
   * Escape and quote a single CSV cell value.
   */
  function formatCSVCell(val) {
    if (val == null) return '""';
    let str = String(val);
    // Replace null bytes and escape quotes
    str = str.replace(/"/g, '""');
    return `"${str}"`;
  }

  /**
   * Export raw headers and row arrays to a downloadable CSV file.
   *
   * @param {string} filename - Desired filename, e.g. "portfolio_holdings.csv"
   * @param {Array<string>} headers - Column headers
   * @param {Array<Array<any>>} rows - Matrix of cell values
   */
  function exportDataToCSV(filename, headers, rows) {
    if (!filename) filename = "portfolioiq_export.csv";
    if (!filename.toLowerCase().endsWith(".csv")) filename += ".csv";

    const headerLine = headers.map(formatCSVCell).join(",");
    const rowLines = rows.map((r) => r.map(formatCSVCell).join(","));
    const csvContent = [headerLine, ...rowLines].join("\r\n");

    // Prepend UTF-8 BOM (\uFEFF) so Excel on Windows properly displays Rupee symbols (₹) and characters
    const blob = new Blob(["\uFEFF" + csvContent], {
      type: "text/csv;charset=utf-8;",
    });

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.setAttribute("download", filename);
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();

    setTimeout(() => {
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    }, 100);

    if (typeof window.showToast === "function") {
      window.showToast(`Exported ${filename} successfully.`, "success");
    }
  }

  /**
   * Extract table content and trigger CSV download.
   *
   * @param {string|HTMLTableElement} tableSelectorOrEl - Element or CSS selector for the table
   * @param {string} [filename] - Optional filename
   */
  function exportTableToCSV(tableSelectorOrEl, filename) {
    const table =
      typeof tableSelectorOrEl === "string"
        ? document.querySelector(tableSelectorOrEl)
        : tableSelectorOrEl;

    if (!table) {
      console.warn("exportTableToCSV: Table not found", tableSelectorOrEl);
      if (typeof window.showToast === "function") {
        window.showToast("Could not find table to export.", "warn");
      }
      return;
    }

    if (!filename) {
      const tableId = table.id || "table";
      const dateStr = new Date().toISOString().split("T")[0];
      filename = `${tableId}_${dateStr}.csv`;
    }

    // 1. Extract Headers (skip .no-export columns)
    const headerCols = [];
    const headerIndices = [];
    const ths = table.querySelectorAll("thead th");

    if (ths.length > 0) {
      ths.forEach((th, idx) => {
        if (!th.classList.contains("no-export") && th.offsetParent !== null) {
          headerIndices.push(idx);
          // Strip sorting arrows or extra badges
          const text = th.innerText.replace(/[↕▲▼\n]/g, " ").trim();
          headerCols.push(text);
        }
      });
    }

    // 2. Extract Body Rows
    const rows = [];
    const trs = table.querySelectorAll("tbody tr");

    trs.forEach((tr) => {
      // Ignore hidden or empty-state rows
      if (tr.classList.contains("no-export") || tr.querySelector(".empty-state")) return;

      const rowCells = [];
      const tds = tr.querySelectorAll("td");
      if (tds.length === 0) return;

      if (headerIndices.length > 0) {
        headerIndices.forEach((idx) => {
          const td = tds[idx];
          if (td) {
            let val = td.innerText.replace(/[▲▼\r]/g, "").trim();
            rowCells.push(val);
          } else {
            rowCells.push("");
          }
        });
      } else {
        tds.forEach((td) => {
          if (!td.classList.contains("no-export")) {
            rowCells.push(td.innerText.replace(/[▲▼\r]/g, "").trim());
          }
        });
      }

      rows.push(rowCells);
    });

    if (headerCols.length === 0 && rows.length === 0) {
      if (typeof window.showToast === "function") {
        window.showToast("Table is empty. Nothing to export.", "info");
      }
      return;
    }

    exportDataToCSV(filename, headerCols, rows);
  }

  /**
   * Bind all elements matching [data-export-table] automatically.
   */
  function bindExportButtons() {
    document.querySelectorAll("[data-export-table], [data-export]").forEach((btn) => {
      if (btn.dataset.exportBound) return;
      btn.dataset.exportBound = "true";

      btn.addEventListener("click", (e) => {
        e.preventDefault();
        const targetId = btn.getAttribute("data-export-table") || btn.getAttribute("data-export");
        const filename = btn.getAttribute("data-export-filename") || `${targetId}.csv`;
        exportTableToCSV(`#${targetId}`, filename);
      });
    });
  }

  // Global delegation for dynamically injected tables & buttons
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-export-table], [data-export]");
    if (!btn || btn.dataset.exportBound) return;
    e.preventDefault();
    const targetId = btn.getAttribute("data-export-table") || btn.getAttribute("data-export");
    const filename = btn.getAttribute("data-export-filename") || `${targetId}.csv`;
    exportTableToCSV(`#${targetId}`, filename);
  });

  // Public Export API
  window.PortfolioIQExport = {
    exportDataToCSV,
    exportTableToCSV,
    bindExportButtons,
  };

  // Expose convenient global functions
  window.exportDataToCSV = exportDataToCSV;
  window.exportTableToCSV = exportTableToCSV;

  // Auto-bind on load and observe DOM changes
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindExportButtons);
  } else {
    bindExportButtons();
  }
})();
