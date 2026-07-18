(function () {
  "use strict";

  var dataElement = document.getElementById("dashboard-analytics-data");
  if (!dataElement || typeof ApexCharts === "undefined") {
    return;
  }

  var dashboardData = JSON.parse(dataElement.textContent);
  var charts = dashboardData.charts || {};
  var palette = ["#108dff", "#287F71", "#E77636", "#db398a", "#522c8f", "#6c757d"];
  var textColor = getComputedStyle(document.body).getPropertyValue("--bs-body-color") || "#6c757d";

  function baseOptions(type, height) {
    return {
      chart: {
        type: type,
        height: height || 300,
        parentHeightOffset: 0,
        toolbar: { show: false },
        zoom: { enabled: false },
      },
      colors: palette,
      dataLabels: { enabled: false },
      grid: { strokeDashArray: 3 },
      legend: { position: "bottom", labels: { colors: textColor } },
      tooltip: {
        y: {
          formatter: function (value) {
            return Number(value || 0).toLocaleString("pt-BR") + " registros";
          },
        },
      },
    };
  }

  function renderChart(selector, options) {
    var element = document.querySelector(selector);
    if (!element) {
      return;
    }
    new ApexCharts(element, options).render();
  }

  if (charts.conversations_by_day && charts.conversations_by_day.has_data) {
    var conversationsOptions = baseOptions("area", 310);
    conversationsOptions.series = charts.conversations_by_day.series;
    conversationsOptions.stroke = { curve: "smooth", width: 2 };
    conversationsOptions.fill = {
      type: "gradient",
      gradient: { shadeIntensity: 1, opacityFrom: 0.35, opacityTo: 0.05, stops: [0, 90, 100] },
    };
    conversationsOptions.xaxis = { categories: charts.conversations_by_day.labels, labels: { style: { colors: textColor } } };
    conversationsOptions.yaxis = { min: 0, forceNiceScale: true, labels: { style: { colors: textColor } } };
    renderChart("#chart-conversations-by-day", conversationsOptions);
  }

  if (charts.leads_by_day && charts.leads_by_day.has_data) {
    var leadsOptions = baseOptions("bar", 310);
    leadsOptions.series = charts.leads_by_day.series;
    leadsOptions.plotOptions = { bar: { borderRadius: 4, columnWidth: "45%" } };
    leadsOptions.xaxis = { categories: charts.leads_by_day.labels, labels: { style: { colors: textColor } } };
    leadsOptions.yaxis = { min: 0, forceNiceScale: true, labels: { style: { colors: textColor } } };
    renderChart("#chart-leads-by-day", leadsOptions);
  }

  if (charts.funnel && charts.funnel.has_data) {
    var funnelOptions = baseOptions("bar", 300);
    funnelOptions.series = [{ name: "Leads", data: charts.funnel.series }];
    funnelOptions.plotOptions = { bar: { horizontal: true, borderRadius: 4 } };
    funnelOptions.xaxis = { categories: charts.funnel.labels, labels: { style: { colors: textColor } } };
    funnelOptions.yaxis = { labels: { style: { colors: textColor } } };
    renderChart("#chart-commercial-funnel", funnelOptions);
  }

  if (charts.conversation_states && charts.conversation_states.has_data) {
    var stateOptions = baseOptions("donut", 300);
    stateOptions.series = charts.conversation_states.series;
    stateOptions.labels = charts.conversation_states.labels;
    stateOptions.plotOptions = { pie: { donut: { size: "72%" } } };
    stateOptions.stroke = { width: 0 };
    renderChart("#chart-conversation-states", stateOptions);
  }

  if (charts.tenant_volume && charts.tenant_volume.has_data) {
    var tenantOptions = baseOptions("bar", 340);
    tenantOptions.series = charts.tenant_volume.series;
    tenantOptions.plotOptions = { bar: { horizontal: true, borderRadius: 4 } };
    tenantOptions.xaxis = { categories: charts.tenant_volume.labels, labels: { style: { colors: textColor } } };
    tenantOptions.yaxis = { labels: { style: { colors: textColor } } };
    renderChart("#chart-tenant-volume", tenantOptions);
  }
})();
