// ============================================================
// STRIPE — REQUÊTES NoSQL (MongoDB 7, base stripe_nosql)
// Exécution :  mongosh --file queries/nosql_queries.js
//         ou :  docker exec -i stripe-mongo mongosh --quiet --file /queries/nosql_queries.js
// Chaque pipeline se termine par .forEach(printjson) pour afficher le
// résultat aussi bien en mode script qu'en mode interactif.
// Les dates sont de vrais BSON Date (chargées en Extended JSON {$date}).
// ============================================================

db = db.getSiblingDB("stripe_nosql");
const DAY = 24 * 60 * 60 * 1000;
function section(title) { print("\n" + "─".repeat(70) + "\n" + title + "\n" + "─".repeat(70)); }


// ────────────────────────────────────────────────────────────
// Q1. Logs error/critical des 30 derniers jours, agrégés par service
//     Cas d'usage : monitoring ops, alerting (prod : 24 h)
//     $match en tête → filtre sur l'index de la time-series collection
// ────────────────────────────────────────────────────────────
section("Q1 — Logs critiques par service (30 j)");
db.logs.aggregate([
  { $match: {
      "meta.severity": { $in: ["error", "critical"] },
      timestamp: { $gte: new Date(Date.now() - 30 * DAY) }
  } },
  { $group: {
      _id:            { service: "$meta.service", severity: "$meta.severity" },
      count:          { $sum: 1 },
      avg_latency_ms: { $avg: "$metadata.latency_ms" },
      max_latency_ms: { $max: "$metadata.latency_ms" },
      sample_messages: { $push: "$message" }
  } },
  { $project: {
      _id: 0,
      service: "$_id.service", severity: "$_id.severity", count: 1,
      avg_latency_ms: { $round: ["$avg_latency_ms", 0] }, max_latency_ms: 1,
      sample_messages: { $slice: ["$sample_messages", 2] }
  } },
  { $sort: { count: -1 } }
]).forEach(printjson);


// ────────────────────────────────────────────────────────────
// Q2. Sessions suspectes : non converties, > 8 events, clics répétés sur "payer"
//     Cas d'usage : détection de bots / test de cartes
//     Index utilisé : { converted: 1, event_count: -1 } (règle ESR)
// ────────────────────────────────────────────────────────────
section("Q2 — Sessions suspectes (bots, clics répétés au checkout)");
db.user_sessions.aggregate([
  { $match: { converted: false, event_count: { $gt: 8 } } },
  { $addFields: {
      pay_clicks: { $size: { $filter: {
          input: "$events", as: "e", cond: { $eq: ["$$e.element", "pay_button"] }
      } } }
  } },
  { $match: { pay_clicks: { $gte: 5 } } },
  { $project: {
      _id: 1, customer_id: 1, session_start: 1, duration_seconds: 1,
      event_count: 1, pay_clicks: 1,
      device: "$device.type", country: "$geo.country_code", ip: "$geo.ip_address"
  } },
  { $sort: { pay_clicks: -1, event_count: -1 } },
  { $limit: 10 }
]).forEach(printjson);


// ────────────────────────────────────────────────────────────
// Q3. Transactions à haut risque avec les 3 facteurs SHAP explicatifs
//     Cas d'usage : explicabilité (RGPD art. 22), audit des décisions
//     Index utilisé : { "prediction.risk_level": 1, computed_at: -1 }
// ────────────────────────────────────────────────────────────
section("Q3 — Transactions à haut risque + facteurs explicatifs (SHAP)");
db.ml_features.aggregate([
  { $match: { "prediction.risk_level": { $in: ["high", "critical"] } } },
  { $sort: { computed_at: -1 } },
  { $limit: 10 },
  { $project: {
      _id: 0, transaction_id: 1, customer_id: 1, computed_at: 1,
      fraud_probability: "$prediction.fraud_probability",
      risk_level:        "$prediction.risk_level",
      action_taken:      "$prediction.action_taken",
      model_version:     "$prediction.model_version",
      top_factors:       "$prediction.top_shap",
      geo_mismatch:      "$features.geo_mismatch",
      velocity_1h:       "$features.transactions_last_1h",
      ip_reputation:     "$features.ip_reputation_score",
      label:             "$label.is_fraud"
  } }
]).forEach(printjson);


// ────────────────────────────────────────────────────────────
// Q4. Feedbacks négatifs (score <= 2) par pays, avec les tags les plus fréquents
//     Cas d'usage : support client, priorisation produit
//     $unwind + $group sur les tags = vrai comptage de fréquence
//     country_code est dénormalisé dans le document (extended reference)
// ────────────────────────────────────────────────────────────
section("Q4 — Feedbacks négatifs par pays + top tags");
db.customer_feedback.aggregate([
  { $match: { "scores.overall": { $lte: 2 } } },
  { $unwind: "$tags" },
  { $group: {
      _id:            { country: "$country_code", tag: "$tags" },
      count:          { $sum: 1 },
      avg_score:      { $avg: "$scores.overall" },
      avg_nps:        { $avg: "$nps_score" },
      feedback_ids:   { $addToSet: "$_id" }
  } },
  { $sort: { "_id.country": 1, count: -1 } },
  { $group: {
      _id:                "$_id.country",
      negative_feedbacks: { $sum: { $size: "$feedback_ids" } },
      avg_score:          { $avg: "$avg_score" },
      avg_nps:            { $avg: "$avg_nps" },
      top_tags:           { $push: { tag: "$_id.tag", count: "$count" } }
  } },
  { $project: {
      _id: 0, country: "$_id", negative_feedbacks: 1,
      avg_score: { $round: ["$avg_score", 2] }, avg_nps: { $round: ["$avg_nps", 1] },
      top_tags: { $slice: ["$top_tags", 3] }
  } },
  { $sort: { negative_feedbacks: -1 } }
]).forEach(printjson);


// ────────────────────────────────────────────────────────────
// Q5. Latence P95 par service sur 7 jours (SLA)
//     $percentile est disponible depuis MongoDB 7.0 (t-digest).
//     Fallback < 7.0 en commentaire sous la requête.
// ────────────────────────────────────────────────────────────
section("Q5 — Latence P50 / P95 par service (7 j)");
db.logs.aggregate([
  { $match: {
      "meta.type": "access",
      timestamp: { $gte: new Date(Date.now() - 7 * DAY) },
      "metadata.latency_ms": { $exists: true }
  } },
  { $group: {
      _id:        "$meta.service",
      requests:   { $sum: 1 },
      p50_p95:    { $percentile: { input: "$metadata.latency_ms", p: [0.5, 0.95], method: "approximate" } },
      max_ms:     { $max: "$metadata.latency_ms" },
      error_rate: { $avg: { $cond: [{ $gte: ["$metadata.http_status", 500] }, 1, 0] } }
  } },
  { $project: {
      _id: 0, service: "$_id", requests: 1,
      p50_ms: { $round: [{ $arrayElemAt: ["$p50_p95", 0] }, 0] },
      p95_ms: { $round: [{ $arrayElemAt: ["$p50_p95", 1] }, 0] },
      max_ms: 1,
      error_rate_pct: { $round: [{ $multiply: ["$error_rate", 100] }, 2] }
  } },
  { $sort: { p95_ms: -1 } }
]).forEach(printjson);
// Fallback MongoDB < 7.0 : trier puis prendre l'élément au rang 95 %
//   { $sort: { "metadata.latency_ms": 1 } },
//   { $group: { _id: "$meta.service", lat: { $push: "$metadata.latency_ms" } } },
//   { $project: { p95_ms: { $arrayElemAt: ["$lat", { $floor: { $multiply: [0.95, { $size: "$lat" }] } }] } } }


// ────────────────────────────────────────────────────────────
// Q6. Qualité du modèle sur données labellisées (boucle de feedback)
//     Précision / rappel par niveau de risque, uniquement sur les
//     transactions dont le label est connu (chargeback ou maturité > 120 j).
//     Cas d'usage : monitoring de performance, décision de réentraînement
// ────────────────────────────────────────────────────────────
section("Q6 — Performance du modèle sur les transactions labellisées");
db.ml_features.aggregate([
  { $match: { "label.is_fraud": { $ne: null } } },
  { $group: {
      _id:      "$prediction.risk_level",
      n:        { $sum: 1 },
      frauds:   { $sum: { $cond: ["$label.is_fraud", 1, 0] } },
      avg_prob: { $avg: "$prediction.fraud_probability" }
  } },
  { $project: {
      _id: 0, risk_level: "$_id", labeled: "$n", confirmed_frauds: "$frauds",
      precision_pct: { $round: [{ $multiply: [{ $divide: ["$frauds", "$n"] }, 100] }, 1] },
      avg_probability: { $round: ["$avg_prob", 3] }
  } },
  { $sort: { avg_probability: -1 } }
]).forEach(printjson);

// Rappel global : part des fraudes confirmées que le modèle avait classées high/critical
db.ml_features.aggregate([
  { $match: { "label.is_fraud": true } },
  { $group: {
      _id: null,
      confirmed_frauds: { $sum: 1 },
      caught: { $sum: { $cond: [{ $in: ["$prediction.risk_level", ["high", "critical"]] }, 1, 0] } }
  } },
  { $project: { _id: 0, confirmed_frauds: 1, caught: 1,
                recall_pct: { $round: [{ $multiply: [{ $divide: ["$caught", "$confirmed_frauds"] }, 100] }, 1] } } }
]).forEach(printjson);


// ────────────────────────────────────────────────────────────
// Q7. Entonnoir de checkout ($facet : plusieurs agrégations en une passe)
//     Cas d'usage : analyse comportementale, personnalisation
// ────────────────────────────────────────────────────────────
section("Q7 — Entonnoir de conversion par device ($facet)");
db.user_sessions.aggregate([
  { $facet: {
      funnel: [
        { $unwind: "$events" },
        { $match: { "events.event": "page_view" } },
        { $group: { _id: "$events.page", sessions: { $addToSet: "$_id" } } },
        { $project: { _id: 0, page: "$_id", sessions: { $size: "$sessions" } } },
        { $sort: { sessions: -1 } }
      ],
      by_device: [
        { $group: {
            _id: "$device.type",
            sessions: { $sum: 1 },
            converted: { $sum: { $cond: ["$converted", 1, 0] } },
            avg_duration_s: { $avg: "$duration_seconds" }
        } },
        { $project: { _id: 0, device: "$_id", sessions: 1, converted: 1,
                      conversion_pct: { $round: [{ $multiply: [{ $divide: ["$converted", "$sessions"] }, 100] }, 1] },
                      avg_duration_s: { $round: ["$avg_duration_s", 0] } } },
        { $sort: { conversion_pct: -1 } }
      ]
  } }
]).forEach(printjson);


// ────────────────────────────────────────────────────────────
// Q8. Jointure NoSQL ↔ NoSQL ($lookup) : recommandations du mois
//     enrichies avec le NPS moyen du merchant
//     (la jointure vers l'OLTP se fait côté application ou ETL, jamais ici)
// ────────────────────────────────────────────────────────────
section("Q8 — Recommandations enrichies du NPS merchant ($lookup)");
db.recommendations.aggregate([
  { $match: { valid_until: { $gte: new Date() } } },
  { $lookup: {
      from: "customer_feedback",
      localField: "merchant_id",
      foreignField: "merchant_id",
      pipeline: [{ $group: { _id: null, avg_nps: { $avg: "$nps_score" }, n: { $sum: 1 } } }],
      as: "feedback"
  } },
  { $unwind: "$feedback" },
  { $project: {
      _id: 0, merchant_id: 1, model_version: 1,
      feedbacks: "$feedback.n", avg_nps: { $round: ["$feedback.avg_nps", 1] },
      best_recommendation: { $arrayElemAt: ["$recommendations", 0] }
  } },
  { $sort: { avg_nps: 1 } },
  { $limit: 5 }
]).forEach(printjson);


// ────────────────────────────────────────────────────────────
// Q9. Transaction multi-documents (ACID côté MongoDB, replica set requis)
//     Le scoring écrit atomiquement la prédiction ET l'alerte : soit les
//     deux existent, soit aucun.
// ────────────────────────────────────────────────────────────
section("Q9 — Transaction multi-documents (ml_features + fraud_alerts)");
const session = db.getMongo().startSession();
try {
  session.withTransaction(() => {
    const sdb = session.getDatabase("stripe_nosql");
    const txId = "ch_demo_" + Math.random().toString(16).slice(2, 10);
    sdb.ml_features.insertOne({
      transaction_id: txId, customer_id: "cus_demo", merchant_id: "mer_demo",
      computed_at: new Date(), model_target: "fraud_detection",
      features: { geo_mismatch: true, transactions_last_1h: 4 },
      prediction: { fraud_probability: 0.91, risk_level: "critical", action_taken: "block",
                    model_version: "fraud-xgb-v2.4.1" },
      label: { is_fraud: null, source: null, labeled_at: null }
    });
    sdb.fraud_alerts.insertOne({ transaction_id: txId, level: "critical", created_at: new Date(), acknowledged: false });
    print("  écritures atomiques OK pour " + txId);
  });
} finally {
  session.endSession();
}
printjson(db.fraud_alerts.findOne({}, { _id: 0 }));
// Nettoyage de la démo
db.ml_features.deleteMany({ customer_id: "cus_demo" });
db.fraud_alerts.drop();


// ────────────────────────────────────────────────────────────
// Q10. Plan d'exécution : l'index composé est bien utilisé (IXSCAN)
// ────────────────────────────────────────────────────────────
section("Q10 — explain() : index { prediction.risk_level, computed_at }");
const plan = db.ml_features.find({ "prediction.risk_level": "critical" }).sort({ computed_at: -1 }).limit(5)
  .explain("executionStats");
printjson({
  stage:          plan.queryPlanner.winningPlan.stage,
  input_stage:    plan.queryPlanner.winningPlan.inputStage?.stage ?? plan.queryPlanner.winningPlan.inputStage?.inputStage?.stage,
  index_used:     plan.queryPlanner.winningPlan.inputStage?.indexName ?? plan.queryPlanner.winningPlan.inputStage?.inputStage?.indexName,
  docs_examined:  plan.executionStats.totalDocsExamined,
  keys_examined:  plan.executionStats.totalKeysExamined,
  returned:       plan.executionStats.nReturned
});
