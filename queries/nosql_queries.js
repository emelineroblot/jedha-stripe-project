// ============================================================
// STRIPE — REQUÊTES NoSQL (MongoDB)
// À exécuter dans mongosh ou MongoDB Compass
// ============================================================


// ────────────────────────────────────────────────────────────
// Q1. Logs d'erreurs critiques des dernières 24 heures
//     Agrégation par service et par type d'erreur
//     Cas d'usage : monitoring ops, alerting
// ────────────────────────────────────────────────────────────
db.logs.aggregate([
  {
    $match: {
      severity: { $in: ["error", "critical"] },
      timestamp: {
        $gte: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()
      }
    }
  },
  {
    $group: {
      _id: {
        service:  "$service",
        severity: "$severity"
      },
      count:           { $sum: 1 },
      avg_latency_ms:  { $avg: "$metadata.latency_ms" },
      max_latency_ms:  { $max: "$metadata.latency_ms" },
      sample_messages: { $push: "$message" }
    }
  },
  {
    // Limiter les exemples de messages à 3 par groupe
    $addFields: {
      sample_messages: { $slice: ["$sample_messages", 3] }
    }
  },
  { $sort: { count: -1 } }
]);


// ────────────────────────────────────────────────────────────
// Q2. Sessions suspectes : plus de 10 events sur le checkout
//     sans aboutir à une transaction
//     Cas d'usage : détection de bots, scraping, abus
// ────────────────────────────────────────────────────────────
db.user_sessions.aggregate([
  {
    // Sessions sans transaction associée
    $match: {
      transaction_id: { $exists: false }
    }
  },
  {
    $addFields: {
      event_count:      { $size: "$events" },
      checkout_events:  {
        $size: {
          $filter: {
            input: "$events",
            as:    "e",
            cond:  { $eq: ["$$e.page", "/checkout"] }
          }
        }
      }
    }
  },
  {
    $match: {
      event_count: { $gt: 10 }
    }
  },
  {
    $project: {
      customer_id:     1,
      session_start:   1,
      duration_seconds: 1,
      event_count:     1,
      checkout_events: 1,
      "device.type":   1,
      "geo.country_code": 1,
      "geo.ip_address": 1
    }
  },
  { $sort: { event_count: -1 } },
  { $limit: 50 }
]);


// ────────────────────────────────────────────────────────────
// Q3. Features ML des transactions à risque élevé
//     avec les top facteurs contributifs
//     Cas d'usage : explication des décisions du modèle (XAI),
//                  audit fraude
// ────────────────────────────────────────────────────────────
db.ml_features.aggregate([
  {
    $match: {
      "prediction.risk_level": { $in: ["high", "critical"] },
      "prediction.fraud_probability": { $gte: 0.7 }
    }
  },
  {
    $project: {
      transaction_id:             1,
      customer_id:                1,
      computed_at:                1,
      fraud_probability:          "$prediction.fraud_probability",
      risk_level:                 "$prediction.risk_level",
      model_version:              "$prediction.model_version",
      // Facteurs de risque clés
      velocity_score:             "$features.velocity_score",
      transactions_last_1h:       "$features.transactions_last_1h",
      distinct_countries_30d:     "$features.distinct_countries_30d",
      ip_reputation_score:        "$features.ip_reputation_score",
      device_seen_before:         "$features.device_seen_before",
      amount_zscore:              "$features.amount_zscore"
    }
  },
  { $sort: { fraud_probability: -1 } },
  { $limit: 100 }
]);


// ────────────────────────────────────────────────────────────
// Q4. Feedbacks négatifs (score < 3) agrégés par pays
//     avec analyse de sentiment et thèmes récurrents
//     Cas d'usage : support client, amélioration produit
// ────────────────────────────────────────────────────────────
db.customer_feedback.aggregate([
  {
    $match: {
      "scores.overall": { $lt: 3 }
    }
  },
  {
    // Jointure avec la collection customers via customer_id
    // (lookup simulé — en pratique customer_id référence PostgreSQL)
    $group: {
      _id: {
        // Le pays n'est pas dans feedback, on groupe par merchant
        merchant_id: "$merchant_id",
        sentiment:   "$sentiment.label"
      },
      feedback_count:    { $sum: 1 },
      avg_overall_score: { $avg: "$scores.overall" },
      avg_nps:           { $avg: "$nps_score" },
      all_tags:          { $push: "$tags" },
      sample_texts:      { $push: "$text" }
    }
  },
  {
    $addFields: {
      // Aplatir le tableau de tableaux de tags
      all_tags_flat: {
        $reduce: {
          input:       "$all_tags",
          initialValue: [],
          in: { $concatArrays: ["$$value", "$$this"] }
        }
      },
      sample_texts: { $slice: ["$sample_texts", 2] }
    }
  },
  {
    $project: {
      merchant_id:       "$_id.merchant_id",
      sentiment:         "$_id.sentiment",
      feedback_count:    1,
      avg_overall_score: { $round: ["$avg_overall_score", 2] },
      avg_nps:           { $round: ["$avg_nps", 1] },
      sample_texts:      1,
      // Top 5 tags les plus fréquents
      top_tags: {
        $slice: [
          { $setUnion: "$all_tags_flat" },
          5
        ]
      }
    }
  },
  { $sort: { feedback_count: -1 } }
]);


// ────────────────────────────────────────────────────────────
// Q5. Statistiques de latence par service sur 7 jours
//     avec détection des pics (latence > 2x la médiane)
//     Cas d'usage : SLA monitoring, capacity planning
// ────────────────────────────────────────────────────────────
db.logs.aggregate([
  {
    $match: {
      type: "error",
      timestamp: {
        $gte: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString()
      },
      "metadata.latency_ms": { $exists: true }
    }
  },
  {
    $group: {
      _id:             "$service",
      total_errors:    { $sum: 1 },
      avg_latency_ms:  { $avg: "$metadata.latency_ms" },
      max_latency_ms:  { $max: "$metadata.latency_ms" },
      min_latency_ms:  { $min: "$metadata.latency_ms" },
      p95_approx: {
        // Approximation P95 : max des 5% les plus hauts
        $percentile: {
          input:  "$metadata.latency_ms",
          p:      [0.95],
          method: "approximate"
        }
      },
      http_status_counts: {
        $push: "$metadata.http_status"
      }
    }
  },
  {
    $addFields: {
      avg_latency_ms: { $round: ["$avg_latency_ms", 0] }
    }
  },
  { $sort: { avg_latency_ms: -1 } }
]);
