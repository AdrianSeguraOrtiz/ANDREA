args <- commandArgs(trailingOnly = TRUE)
options(bspm.sudo = TRUE)

parse_args <- function(values) {
  out <- list()
  idx <- 1
  while (idx <= length(values)) {
    key <- values[[idx]]
    if (!startsWith(key, "--")) {
      stop(sprintf("Unexpected argument: %s", key), call. = FALSE)
    }
    if (idx == length(values)) {
      stop(sprintf("Missing value for argument: %s", key), call. = FALSE)
    }
    out[[substring(key, 3)]] <- values[[idx + 1]]
    idx <- idx + 2
  }
  out
}

`%||%` <- function(lhs, rhs) {
  if (is.null(lhs)) rhs else lhs
}

parsed <- parse_args(args)
required_keys <- c("request", "output-dir")
missing_keys <- required_keys[!required_keys %in% names(parsed)]
if (length(missing_keys) > 0) {
  stop(
    sprintf("Missing required arguments: %s", paste(missing_keys, collapse = ", ")),
    call. = FALSE
  )
}

suppressPackageStartupMessages({
  library(dyngen)
  library(jsonlite)
  library(Matrix)
})

request_path <- normalizePath(parsed[["request"]], mustWork = TRUE)
output_dir <- normalizePath(parsed[["output-dir"]], mustWork = FALSE)
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(file.path(output_dir, "extras"), recursive = TRUE, showWarnings = FALSE)
dir.create(file.path(output_dir, "truth"), recursive = TRUE, showWarnings = FALSE)
dir.create(file.path(output_dir, "provenance", "raw"), recursive = TRUE, showWarnings = FALSE)

progress_path <- file.path(output_dir, "progress.json")
raw_dir <- file.path(output_dir, "provenance", "raw")

write_json_atomic <- function(path, value) {
  tmp <- sprintf("%s.tmp", path)
  jsonlite::write_json(value, path = tmp, auto_unbox = TRUE, pretty = TRUE, null = "null")
  if (file.exists(path)) {
    file.remove(path)
  }
  ok <- file.rename(tmp, path)
  if (!ok) {
    stop(sprintf("Failed to write %s atomically.", path), call. = FALSE)
  }
}

write_progress <- function(status, phase, message = NULL, details = list()) {
  payload <- c(
    list(
      schema_version = "1.0",
      status = status,
      phase = phase,
      updated_at = format(Sys.time(), tz = "UTC", usetz = TRUE)
    ),
    if (!is.null(message)) list(message = message) else list(),
    if (length(details) > 0) list(details = details) else list()
  )
  write_json_atomic(progress_path, payload)
}

parse_request <- function(path) {
  req <- jsonlite::read_json(path, simplifyVector = TRUE)
  required_fields <- c("simulator_id", "profile", "seed", "effective_extras", "params", "runtime_resources")
  missing_fields <- required_fields[!required_fields %in% names(req)]
  if (length(missing_fields) > 0) {
    stop(
      sprintf(
        "simulator-run-request.json is missing required fields: %s",
        paste(missing_fields, collapse = ", ")
      ),
      call. = FALSE
    )
  }
  req
}

normalise_runtime_resources <- function(req) {
  resources <- req$runtime_resources
  raw_threads <- resources$threads %||% 1L
  if (
    length(raw_threads) != 1L ||
      is.na(raw_threads) ||
      raw_threads != floor(raw_threads) ||
      raw_threads < 1L
  ) {
    stop("runtime_resources.threads must be an integer >= 1.", call. = FALSE)
  }
  threads <- as.integer(raw_threads)
  list(threads = threads)
}

normalise_params <- function(req) {
  params <- req$params
  list(
    backbone_template = as.character(params$backbone_template %||% "bifurcating"),
    num_tfs = if (is.null(params$num_tfs)) NULL else as.integer(params$num_tfs),
    num_cells = as.integer(params$num_cells %||% 100L),
    num_targets = as.integer(params$num_targets %||% 50L),
    num_hks = as.integer(params$num_hks %||% 50L),
    distance_metric = as.character(params$distance_metric %||% "pearson"),
    tf_network_params = list(
      min_tfs_per_module = as.integer((params$tf_network_params$min_tfs_per_module) %||% 1L),
      weighted_sampling = isTRUE((params$tf_network_params$weighted_sampling) %||% FALSE)
    ),
    feature_network_params = list(
      realnet = if (is.null(params$feature_network_params$realnet)) NULL else as.character(params$feature_network_params$realnet),
      damping = as.numeric((params$feature_network_params$damping) %||% 0.01),
      target_resampling = if (is.null(params$feature_network_params$target_resampling)) NULL else as.integer(params$feature_network_params$target_resampling),
      max_in_degree = as.integer((params$feature_network_params$max_in_degree) %||% 5L)
    ),
    gold_standard_params = list(
      tau = as.numeric((params$gold_standard_params$tau) %||% (30 / 3600)),
      census_interval = as.numeric((params$gold_standard_params$census_interval) %||% (10 / 60)),
      simulate_targets = isTRUE((params$gold_standard_params$simulate_targets) %||% FALSE)
    ),
    simulation_params = list(
      burn_time = if (is.null(params$simulation_params$burn_time)) NULL else as.numeric(params$simulation_params$burn_time),
      total_time = if (is.null(params$simulation_params$total_time)) NULL else as.numeric(params$simulation_params$total_time),
      ssa_etl_tau = as.numeric((params$simulation_params$ssa_etl_tau) %||% (30 / 3600)),
      census_interval = as.numeric((params$simulation_params$census_interval) %||% 4),
      num_simulations = as.integer((params$simulation_params$num_simulations) %||% 32L),
      num_knockdown_simulations = as.integer((params$simulation_params$num_knockdown_simulations) %||% 0L),
      store_reaction_firings = isTRUE((params$simulation_params$store_reaction_firings) %||% FALSE),
      store_reaction_propensities = isTRUE((params$simulation_params$store_reaction_propensities) %||% FALSE),
      compute_dimred = isTRUE((params$simulation_params$compute_dimred) %||% FALSE),
      compute_rna_velocity = isTRUE((params$simulation_params$compute_rna_velocity) %||% FALSE),
      kinetics_noise_kind = as.character((params$simulation_params$kinetics_noise_kind) %||% "simple"),
      kinetics_noise_mean = as.numeric((params$simulation_params$kinetics_noise_mean) %||% 1.0),
      kinetics_noise_sd = as.numeric((params$simulation_params$kinetics_noise_sd) %||% 0.005)
    ),
    experiment_params = list(
      kind = as.character((params$experiment_params$kind) %||% "snapshot"),
      map_reference_cpm = isTRUE((params$experiment_params$map_reference_cpm) %||% FALSE),
      map_reference_ls = isTRUE((params$experiment_params$map_reference_ls) %||% FALSE),
      weight_bw = as.numeric((params$experiment_params$weight_bw) %||% 0.1),
      num_timepoints = as.integer((params$experiment_params$num_timepoints) %||% 8L),
      pct_between = as.numeric((params$experiment_params$pct_between) %||% 0.75)
    )
  )
}

load_backbone <- function(backbone_template) {
  fn_name <- paste0("backbone_", backbone_template)
  ns <- asNamespace("dyngen")
  if (!exists(fn_name, envir = ns, inherits = FALSE)) {
    stop(
      sprintf("Unsupported dyngen backbone_template: %s", backbone_template),
      call. = FALSE
    )
  }
  get(fn_name, envir = ns, inherits = FALSE)()
}

build_tf_network_params <- function(params) {
  dyngen::tf_network_default(
    min_tfs_per_module = params$tf_network_params$min_tfs_per_module,
    weighted_sampling = params$tf_network_params$weighted_sampling
  )
}

build_feature_network_params <- function(params) {
  dyngen::feature_network_default(
    realnet = params$feature_network_params$realnet,
    damping = params$feature_network_params$damping,
    target_resampling = if (is.null(params$feature_network_params$target_resampling)) Inf else params$feature_network_params$target_resampling,
    max_in_degree = params$feature_network_params$max_in_degree
  )
}

build_gold_standard_params <- function(params) {
  dyngen::gold_standard_default(
    tau = params$gold_standard_params$tau,
    census_interval = params$gold_standard_params$census_interval,
    simulate_targets = params$gold_standard_params$simulate_targets
  )
}

build_kinetics_noise_function <- function(params) {
  kind <- params$simulation_params$kinetics_noise_kind
  if (identical(kind, "none")) {
    return(dyngen::kinetics_noise_none())
  }
  dyngen::kinetics_noise_simple(
    mean = params$simulation_params$kinetics_noise_mean,
    sd = params$simulation_params$kinetics_noise_sd
  )
}

build_simulation_experiment_types <- function(params) {
  dplyr::bind_rows(
    dyngen::simulation_type_wild_type(
      num_simulations = params$simulation_params$num_simulations
    ),
    dyngen::simulation_type_knockdown(
      num_simulations = params$simulation_params$num_knockdown_simulations
    )
  )
}

build_simulation_params <- function(params, need_cellwise_grn, need_rna_velocity) {
  dyngen::simulation_default(
    burn_time = params$simulation_params$burn_time,
    total_time = params$simulation_params$total_time,
    ssa_algorithm = dyngen::ssa_etl(tau = params$simulation_params$ssa_etl_tau),
    census_interval = params$simulation_params$census_interval,
    experiment_params = build_simulation_experiment_types(params),
    store_reaction_firings = params$simulation_params$store_reaction_firings,
    store_reaction_propensities = params$simulation_params$store_reaction_propensities,
    compute_cellwise_grn = need_cellwise_grn,
    compute_dimred = params$simulation_params$compute_dimred,
    compute_rna_velocity = isTRUE(params$simulation_params$compute_rna_velocity) || isTRUE(need_rna_velocity),
    kinetics_noise_function = build_kinetics_noise_function(params)
  )
}

build_experiment_params <- function(params) {
  kind <- params$experiment_params$kind
  if (identical(kind, "synchronised")) {
    return(
      dyngen::experiment_synchronised(
        map_reference_cpm = params$experiment_params$map_reference_cpm,
        map_reference_ls = params$experiment_params$map_reference_ls,
        num_timepoints = params$experiment_params$num_timepoints,
        pct_between = params$experiment_params$pct_between
      )
    )
  }
  dyngen::experiment_snapshot(
    map_reference_cpm = params$experiment_params$map_reference_cpm,
    map_reference_ls = params$experiment_params$map_reference_ls,
    weight_bw = params$experiment_params$weight_bw
  )
}

write_expression_tsv <- function(counts, path) {
  expr_df <- data.frame(gene = colnames(counts), t(as.matrix(counts)), check.names = FALSE)
  expr_df <- expr_df[, c("gene", rownames(counts)), drop = FALSE]
  write.table(
    expr_df,
    file = path,
    sep = "\t",
    row.names = FALSE,
    col.names = TRUE,
    quote = FALSE
  )
}

write_gene_universe <- function(counts, path) {
  genes <- unique(as.character(colnames(counts)))
  genes <- genes[nzchar(genes)]
  if (length(genes) == 0) {
    stop("truth gene_universe derivation found no generated expression genes.", call. = FALSE)
  }
  writeLines(genes, con = path, sep = "\n", useBytes = TRUE)
}

write_tf_list <- function(feature_info, path) {
  tf_ids <- feature_info$feature_id[feature_info$is_tf]
  tf_ids <- unique(as.character(tf_ids))
  writeLines(tf_ids, con = path, sep = "\n", useBytes = TRUE)
}

write_table_tsv <- function(value, path) {
  table_df <- as.data.frame(value, stringsAsFactors = FALSE)
  write.table(
    table_df,
    file = path,
    sep = "\t",
    row.names = FALSE,
    col.names = TRUE,
    quote = FALSE
  )
}

write_native_outputs <- function(dataset, native_output_ids, output_dir) {
  requested <- unique(as.character(native_output_ids))
  requested <- requested[nzchar(requested)]
  if (length(requested) == 0) {
    return(structure(list(), names = character()))
  }

  dir.create(file.path(output_dir, "native"), recursive = TRUE, showWarnings = FALSE)
  manifest <- list()

  if ("milestone_network" %in% requested) {
    write_table_tsv(dataset$milestone_network, file.path(output_dir, "native", "milestone_network.tsv"))
    manifest$milestone_network <- "native/milestone_network.tsv"
  }
  if ("milestone_percentages" %in% requested) {
    write_table_tsv(
      dataset$milestone_percentages,
      file.path(output_dir, "native", "milestone_percentages.tsv")
    )
    manifest$milestone_percentages <- "native/milestone_percentages.tsv"
  }
  if ("progressions" %in% requested) {
    write_table_tsv(dataset$progressions, file.path(output_dir, "native", "progressions.tsv"))
    manifest$progressions <- "native/progressions.tsv"
  }
  if ("rna_velocity" %in% requested) {
    if (is.null(dataset$rna_velocity)) {
      stop("native output rna_velocity was requested but dyngen did not return dataset$rna_velocity.", call. = FALSE)
    }
    write_expression_tsv(dataset$rna_velocity, file.path(output_dir, "native", "rna_velocity.tsv"))
    manifest$rna_velocity <- "native/rna_velocity.tsv"
  }
  if ("regulatory_network_sc" %in% requested) {
    if (is.null(dataset$regulatory_network_sc)) {
      stop("native output regulatory_network_sc was requested but dyngen did not return dataset$regulatory_network_sc.", call. = FALSE)
    }
    write_table_tsv(
      dataset$regulatory_network_sc,
      file.path(output_dir, "native", "regulatory_network_sc.tsv")
    )
    manifest$regulatory_network_sc <- "native/regulatory_network_sc.tsv"
  }

  manifest
}

derive_groups <- function(milestone_percentages, cell_ids) {
  milestone_df <- as.data.frame(milestone_percentages, stringsAsFactors = FALSE)
  milestone_df$milestone_id <- as.character(milestone_df$milestone_id)
  milestone_df$cell_id <- as.character(milestone_df$cell_id)
  milestone_df$percentage <- as.numeric(milestone_df$percentage)

  assignments <- lapply(split(milestone_df, milestone_df$cell_id), function(df) {
    df <- df[order(-df$percentage, df$milestone_id), , drop = FALSE]
    data.frame(
      cell = df$cell_id[[1]],
      cluster = df$milestone_id[[1]],
      stringsAsFactors = FALSE
    )
  })
  groups_df <- do.call(rbind, assignments)
  groups_df <- groups_df[match(cell_ids, groups_df$cell), , drop = FALSE]
  rownames(groups_df) <- NULL
  groups_df
}

write_groups <- function(groups_df, path) {
  write.table(
    groups_df,
    file = path,
    sep = "\t",
    row.names = FALSE,
    col.names = TRUE,
    quote = FALSE
  )
}

derive_milestone_order <- function(milestone_network, milestone_ids = character()) {
  milestone_df <- unique(as.data.frame(milestone_network, stringsAsFactors = FALSE)[, c("from", "to"), drop = FALSE])
  milestone_df$from <- as.character(milestone_df$from)
  milestone_df$to <- as.character(milestone_df$to)
  ids <- sort(unique(as.character(c(milestone_ids, milestone_df$from, milestone_df$to))))
  ids <- ids[nzchar(ids)]
  if (length(ids) == 0) {
    return(structure(numeric(), names = character()))
  }

  milestone_df <- milestone_df[
    milestone_df$from %in% ids &
      milestone_df$to %in% ids &
      milestone_df$from != milestone_df$to,
    ,
    drop = FALSE
  ]
  incoming <- unique(milestone_df$to)
  roots <- sort(setdiff(ids, incoming))
  if (length(roots) == 0) {
    roots <- ids[[1]]
  }

  distances <- rep(Inf, length(ids))
  names(distances) <- ids
  distances[roots] <- 0
  frontier <- roots
  while (length(frontier) > 0 && nrow(milestone_df) > 0) {
    current <- frontier[[1]]
    frontier <- frontier[-1]
    children <- sort(unique(milestone_df$to[milestone_df$from == current]))
    for (child in children) {
      next_distance <- distances[[current]] + 1
      if (next_distance < distances[[child]]) {
        distances[[child]] <- next_distance
        frontier <- c(frontier, child)
      }
    }
  }

  if (any(is.infinite(distances))) {
    max_finite <- max(distances[is.finite(distances)], 0)
    unreachable <- sort(names(distances)[is.infinite(distances)])
    distances[unreachable] <- max_finite + seq_along(unreachable)
  }

  ordered_ids <- names(distances)[order(distances, names(distances))]
  ranks <- seq_along(ordered_ids) - 1L
  names(ranks) <- ordered_ids
  ranks
}

write_pseudotime <- function(milestone_percentages, cell_ids, milestone_order, path) {
  milestone_df <- as.data.frame(milestone_percentages, stringsAsFactors = FALSE)
  milestone_df$cell_id <- as.character(milestone_df$cell_id)
  milestone_df$milestone_id <- as.character(milestone_df$milestone_id)
  milestone_df$percentage <- as.numeric(milestone_df$percentage)
  milestone_df$order <- as.numeric(milestone_order[milestone_df$milestone_id])
  if (any(is.na(milestone_df$order))) {
    fallback_order <- if (length(milestone_order) == 0) 0 else max(as.numeric(milestone_order), na.rm = TRUE) + 1
    milestone_df$order[is.na(milestone_df$order)] <- fallback_order
  }
  milestone_df$weighted_order <- milestone_df$percentage * milestone_df$order
  weighted <- aggregate(weighted_order ~ cell_id, data = milestone_df, FUN = sum)
  weights <- aggregate(percentage ~ cell_id, data = milestone_df, FUN = sum)
  pseudotime_df <- merge(weighted, weights, by = "cell_id", all = TRUE)
  pseudotime_df$pseudotime_raw <- pseudotime_df$weighted_order / pmax(pseudotime_df$percentage, .Machine$double.eps)
  pseudotime <- pseudotime_df$pseudotime_raw[match(cell_ids, pseudotime_df$cell_id)]
  pseudotime[is.na(pseudotime)] <- 0
  pseudotime_range <- range(pseudotime, na.rm = TRUE)
  if (is.finite(pseudotime_range[[1]]) && diff(pseudotime_range) > 0) {
    pseudotime <- (pseudotime - pseudotime_range[[1]]) / diff(pseudotime_range)
  } else {
    pseudotime <- rep(0, length(cell_ids))
  }
  out <- data.frame(
    cell = as.character(cell_ids),
    pseudotime = as.numeric(pseudotime),
    stringsAsFactors = FALSE
  )
  write.table(out, file = path, sep = "\t", row.names = FALSE, col.names = TRUE, quote = FALSE)
}

write_cell_phenotypes <- function(groups_df, milestone_order, path) {
  phenotype_order <- as.integer(milestone_order[as.character(groups_df$cluster)])
  if (any(is.na(phenotype_order))) {
    fallback_order <- if (length(milestone_order) == 0) 0L else max(as.integer(milestone_order), na.rm = TRUE) + 1L
    phenotype_order[is.na(phenotype_order)] <- fallback_order
  }
  out <- data.frame(
    cell = groups_df$cell,
    phenotype = groups_df$cluster,
    order = phenotype_order,
    stringsAsFactors = FALSE
  )
  write.table(out, file = path, sep = "\t", row.names = FALSE, col.names = TRUE, quote = FALSE)
}

write_cluster_identities <- function(groups_df, milestone_order, path) {
  clusters <- sort(unique(as.character(groups_df$cluster)))
  cluster_order <- as.integer(milestone_order[clusters])
  if (any(is.na(cluster_order))) {
    fallback_order <- if (length(milestone_order) == 0) 0L else max(as.integer(milestone_order), na.rm = TRUE) + 1L
    cluster_order[is.na(cluster_order)] <- fallback_order + seq_len(sum(is.na(cluster_order))) - 1L
  }
  out <- data.frame(
    cluster = clusters,
    annotation = clusters,
    order = cluster_order,
    stringsAsFactors = FALSE
  )
  out <- out[order(out$order, out$cluster), , drop = FALSE]
  write.table(out, file = path, sep = "\t", row.names = FALSE, col.names = TRUE, quote = FALSE)
}

write_enrichment_background <- function(counts, path) {
  genes <- unique(as.character(colnames(counts)))
  genes <- genes[nzchar(genes)]
  if (length(genes) == 0) {
    stop("enrichment_background derivation found no generated expression genes.", call. = FALSE)
  }
  writeLines(genes, con = path, sep = "\n", useBytes = TRUE)
}

write_prior_grn <- function(model, path) {
  feature_network <- unique(as.data.frame(model$feature_network)[, c("from", "to", "strength", "effect")])
  feature_network$from <- as.character(feature_network$from)
  feature_network$to <- as.character(feature_network$to)
  feature_network$strength <- as.numeric(feature_network$strength)
  feature_network$effect <- as.numeric(feature_network$effect)
  prior_df <- data.frame(
    source = feature_network$from,
    target = feature_network$to,
    score = abs(feature_network$strength) * ifelse(feature_network$effect >= 0, 1, -1),
    stringsAsFactors = FALSE
  )
  prior_df <- prior_df[!is.na(prior_df$score) & prior_df$score != 0, , drop = FALSE]
  if (nrow(prior_df) == 0) {
    stop("prior_grn derivation found no nonzero feature-network edges.", call. = FALSE)
  }
  prior_df <- prior_df[order(prior_df$source, prior_df$target), , drop = FALSE]
  write.table(prior_df, file = path, sep = "\t", row.names = FALSE, col.names = TRUE, quote = FALSE)
}

write_prior_grn_by_group <- function(group_edge_activity, path) {
  active_edges <- group_edge_activity[group_edge_activity$active, , drop = FALSE]
  score <- as.numeric(active_edges$mean_signed_strength)
  score[is.na(score)] <- 0
  fallback <- abs(score) <= .Machine$double.eps
  score[fallback] <- as.numeric(active_edges$mean_abs_strength[fallback])
  prior_df <- data.frame(
    group = as.character(active_edges$cluster),
    source = as.character(active_edges$regulator),
    target = as.character(active_edges$target),
    score = score,
    stringsAsFactors = FALSE
  )
  prior_df <- prior_df[!is.na(prior_df$score) & prior_df$score != 0, , drop = FALSE]
  if (nrow(prior_df) == 0) {
    stop("prior_grn_by_group derivation found no active nonzero group-specific edges.", call. = FALSE)
  }
  prior_df <- prior_df[order(prior_df$group, prior_df$source, prior_df$target), , drop = FALSE]
  write.table(prior_df, file = path, sep = "\t", row.names = FALSE, col.names = TRUE, quote = FALSE)
}

derive_group_truth <- function(dataset, groups_df, raw_dir, active_threshold = 0.1) {
  if (is.null(dataset$regulatory_network_sc)) {
    stop("group truth derivation requires dyngen cell-specific GRN output.", call. = FALSE)
  }

  used_groups <- unique(groups_df$cluster)
  if (length(used_groups) < 1) {
    stop("group truth derivation requires at least one exported group.", call. = FALSE)
  }

  regulatory_network_sc <- as.data.frame(dataset$regulatory_network_sc, stringsAsFactors = FALSE)
  regulatory_network_sc$cell_id <- as.character(regulatory_network_sc$cell_id)
  regulatory_network_sc$regulator <- as.character(regulatory_network_sc$regulator)
  regulatory_network_sc$target <- as.character(regulatory_network_sc$target)
  regulatory_network_sc$strength <- as.numeric(regulatory_network_sc$strength)

  edge_activity <- merge(
    regulatory_network_sc,
    groups_df,
    by.x = "cell_id",
    by.y = "cell",
    all.x = FALSE,
    all.y = FALSE
  )

  if (nrow(edge_activity) == 0) {
    group_edge_activity <- data.frame(
      cluster = character(),
      regulator = character(),
      target = character(),
      sum_abs_strength = numeric(),
      sum_signed_strength = numeric(),
      cells_in_group = integer(),
      mean_abs_strength = numeric(),
      mean_signed_strength = numeric(),
      active = logical(),
      edge_id = character(),
      stringsAsFactors = FALSE
    )
  } else {
    edge_activity$abs_strength <- abs(edge_activity$strength)
    sum_abs <- aggregate(
      abs_strength ~ cluster + regulator + target,
      data = edge_activity,
      FUN = sum
    )
    names(sum_abs)[names(sum_abs) == "abs_strength"] <- "sum_abs_strength"
    sum_signed <- aggregate(
      strength ~ cluster + regulator + target,
      data = edge_activity,
      FUN = sum
    )
    names(sum_signed)[names(sum_signed) == "strength"] <- "sum_signed_strength"
    group_edge_activity <- merge(
      sum_abs,
      sum_signed,
      by = c("cluster", "regulator", "target"),
      all = TRUE
    )
    group_counts <- as.data.frame(table(groups_df$cluster), stringsAsFactors = FALSE)
    names(group_counts) <- c("cluster", "cells_in_group")
    group_counts$cluster <- as.character(group_counts$cluster)
    group_counts$cells_in_group <- as.integer(group_counts$cells_in_group)
    group_edge_activity <- merge(
      group_edge_activity,
      group_counts,
      by = "cluster",
      all.x = TRUE,
      all.y = FALSE
    )
    group_edge_activity$sum_abs_strength[is.na(group_edge_activity$sum_abs_strength)] <- 0
    group_edge_activity$sum_signed_strength[is.na(group_edge_activity$sum_signed_strength)] <- 0
    group_edge_activity$mean_abs_strength <- group_edge_activity$sum_abs_strength / pmax(group_edge_activity$cells_in_group, 1L)
    group_edge_activity$mean_signed_strength <- group_edge_activity$sum_signed_strength / pmax(group_edge_activity$cells_in_group, 1L)
    group_edge_activity$active <- group_edge_activity$mean_abs_strength >= active_threshold
    group_edge_activity$edge_id <- paste(
      group_edge_activity$regulator,
      group_edge_activity$target,
      sep = "->"
    )
  }

  group_truth_rows <- lapply(used_groups, function(group_name) {
    group_edges <- group_edge_activity[
      group_edge_activity$cluster == group_name & group_edge_activity$active,
      ,
      drop = FALSE
    ]
    group_truth <- data.frame(
      source = as.character(group_edges$regulator),
      target = as.character(group_edges$target),
      score = as.numeric(group_edges$mean_abs_strength),
      sign = ifelse(group_edges$mean_signed_strength > 0, "+", ifelse(group_edges$mean_signed_strength < 0, "-", "?")),
      evidence = "simulated_truth",
      context = paste0("group:", group_name),
      stringsAsFactors = FALSE
    )
    group_truth <- group_truth[order(group_truth$source, group_truth$target), , drop = FALSE]
    group_truth
  })
  names(group_truth_rows) <- used_groups

  active_edges_by_group <- lapply(used_groups, function(group_name) {
    active <- group_edge_activity[
      group_edge_activity$cluster == group_name & group_edge_activity$active,
      "edge_id",
      drop = TRUE
    ]
    unique(as.character(active))
  })
  names(active_edges_by_group) <- used_groups

  write.table(
    group_edge_activity[
      ,
      c(
        "cluster",
        "regulator",
        "target",
        "mean_abs_strength",
        "mean_signed_strength",
        "cells_in_group",
        "active"
      ),
      drop = FALSE
    ],
    file = file.path(raw_dir, "group_edge_activity.tsv"),
    sep = "\t",
    row.names = FALSE,
    col.names = TRUE,
    quote = FALSE
  )
  write.table(
    group_edge_activity[
      group_edge_activity$active,
      c("cluster", "regulator", "target", "edge_id"),
      drop = FALSE
    ],
    file = file.path(raw_dir, "group_active_networks.tsv"),
    sep = "\t",
    row.names = FALSE,
    col.names = TRUE,
    quote = FALSE
  )
  write.table(
    data.frame(
      group = used_groups,
      context = paste0("group:", used_groups),
      edge_count = vapply(group_truth_rows, nrow, integer(1)),
      stringsAsFactors = FALSE
    ),
    file = file.path(raw_dir, "group_networks_index.tsv"),
    sep = "\t",
    row.names = FALSE,
    col.names = TRUE,
    quote = FALSE
  )

  list(
    group_truth_rows = group_truth_rows,
    active_edges_by_group = active_edges_by_group,
    used_groups = used_groups,
    group_edge_activity = group_edge_activity
  )
}

derive_lineage_tree <- function(dataset, groups_df, active_edges_by_group, path, raw_dir) {
  used_groups <- unique(groups_df$cluster)
  if (length(used_groups) < 2) {
    stop("lineage_tree derivation requires at least two exported groups.", call. = FALSE)
  }

  milestone_network <- unique(as.data.frame(dataset$milestone_network)[, c("from", "to")])
  milestone_network$from <- as.character(milestone_network$from)
  milestone_network$to <- as.character(milestone_network$to)
  lineage_edges <- milestone_network[
    milestone_network$from %in% used_groups &
      milestone_network$to %in% used_groups &
      milestone_network$from != milestone_network$to,
    ,
    drop = FALSE
  ]
  if (nrow(lineage_edges) == 0) {
    stop("lineage_tree derivation found no lineage edges after filtering exported groups.", call. = FALSE)
  }

  lineage_tree <- do.call(
    rbind,
    lapply(seq_len(nrow(lineage_edges)), function(idx) {
      parent <- lineage_edges$from[[idx]]
      child <- lineage_edges$to[[idx]]
      parent_edges <- active_edges_by_group[[parent]]
      child_edges <- active_edges_by_group[[child]]
      gained_edges <- setdiff(child_edges, parent_edges)
      lost_edges <- setdiff(parent_edges, child_edges)
      data.frame(
        child = child,
        parent = parent,
        gain_rate = length(gained_edges) / max(length(child_edges), 1L),
        loss_rate = length(lost_edges) / max(length(parent_edges), 1L),
        stringsAsFactors = FALSE
      )
    })
  )

  write.table(
    lineage_tree,
    file = path,
    sep = "\t",
    row.names = FALSE,
    col.names = TRUE,
    quote = FALSE
  )
  write.table(
    data.frame(group = used_groups, stringsAsFactors = FALSE),
    file = file.path(raw_dir, "lineage_states_used.tsv"),
    sep = "\t",
    row.names = FALSE,
    col.names = TRUE,
    quote = FALSE
  )
  write.table(
    lineage_edges,
    file = file.path(raw_dir, "lineage_transitions_used.tsv"),
    sep = "\t",
    row.names = FALSE,
    col.names = TRUE,
    quote = FALSE
  )
}

derive_global_truth <- function(model) {
  feature_network <- unique(as.data.frame(model$feature_network)[, c("from", "to", "strength", "effect")])
  feature_network$from <- as.character(feature_network$from)
  feature_network$to <- as.character(feature_network$to)
  feature_network$strength <- as.numeric(feature_network$strength)
  feature_network$effect <- as.numeric(feature_network$effect)

  truth_df <- data.frame(
    source = feature_network$from,
    target = feature_network$to,
    score = abs(feature_network$strength),
    sign = ifelse(feature_network$effect >= 0, "+", "-"),
    evidence = "simulated_truth",
    context = "global",
    stringsAsFactors = FALSE
  )

  truth_df <- truth_df[truth_df$score > 0 & truth_df$source != truth_df$target, , drop = FALSE]
  if (nrow(truth_df) == 0) {
    stop("global truth derivation produced no nonzero edges.", call. = FALSE)
  }
  truth_df[order(truth_df$source, truth_df$target), , drop = FALSE]
}

derive_cell_truth <- function(dataset, cell_ids, genes, raw_dir) {
  if (is.null(dataset$regulatory_network_sc)) {
    stop("cell truth derivation requires dyngen cell-specific GRN output.", call. = FALSE)
  }

  regulatory_network_sc <- as.data.frame(dataset$regulatory_network_sc, stringsAsFactors = FALSE)
  required_columns <- c("cell_id", "regulator", "target", "strength")
  missing_columns <- setdiff(required_columns, names(regulatory_network_sc))
  if (length(missing_columns) > 0) {
    stop(
      sprintf(
        "dyngen regulatory_network_sc is missing required columns for cell truth: %s",
        paste(missing_columns, collapse = ", ")
      ),
      call. = FALSE
    )
  }

  regulatory_network_sc$cell_id <- as.character(regulatory_network_sc$cell_id)
  regulatory_network_sc$regulator <- as.character(regulatory_network_sc$regulator)
  regulatory_network_sc$target <- as.character(regulatory_network_sc$target)
  regulatory_network_sc$strength <- as.numeric(regulatory_network_sc$strength)

  valid_cells <- unique(as.character(cell_ids))
  valid_genes <- unique(as.character(genes))
  unknown_cells <- sort(unique(setdiff(unique(regulatory_network_sc$cell_id), valid_cells)))
  if (length(unknown_cells) > 0) {
    stop(
      sprintf(
        "dyngen regulatory_network_sc contains cell IDs absent from expression columns: %s",
        paste(unknown_cells, collapse = ", ")
      ),
      call. = FALSE
    )
  }
  unknown_genes <- sort(unique(setdiff(
    unique(c(regulatory_network_sc$regulator, regulatory_network_sc$target)),
    valid_genes
  )))
  if (length(unknown_genes) > 0) {
    stop(
      sprintf(
        "dyngen regulatory_network_sc contains genes absent from truth/gene_universe.txt: %s",
        paste(unknown_genes, collapse = ", ")
      ),
      call. = FALSE
    )
  }

  cell_truth <- regulatory_network_sc[
    !is.na(regulatory_network_sc$strength) &
      regulatory_network_sc$strength != 0 &
      regulatory_network_sc$regulator != regulatory_network_sc$target,
    ,
    drop = FALSE
  ]
  cell_truth <- data.frame(
    source = as.character(cell_truth$regulator),
    target = as.character(cell_truth$target),
    score = abs(as.numeric(cell_truth$strength)),
    sign = ifelse(cell_truth$strength > 0, "+", "-"),
    evidence = "simulated_truth",
    context = paste0("cell:", cell_truth$cell_id),
    stringsAsFactors = FALSE
  )
  cell_truth <- cell_truth[cell_truth$score > 0 & !is.na(cell_truth$score), , drop = FALSE]
  if (nrow(cell_truth) == 0) {
    stop("cell truth derivation produced no nonzero cell-specific edges.", call. = FALSE)
  }
  cell_truth <- cell_truth[order(cell_truth$context, cell_truth$source, cell_truth$target), , drop = FALSE]

  context_counts <- as.data.frame(table(cell_truth$context), stringsAsFactors = FALSE)
  names(context_counts) <- c("context", "edge_count")
  context_counts$cell <- sub("^cell:", "", context_counts$context)
  context_counts <- context_counts[, c("cell", "context", "edge_count"), drop = FALSE]
  write.table(
    context_counts,
    file = file.path(raw_dir, "cell_networks_index.tsv"),
    sep = "\t",
    row.names = FALSE,
    col.names = TRUE,
    quote = FALSE
  )

  cell_truth
}

write_truth_networks <- function(global_truth, group_truth_rows, cell_truth_rows, output_dir) {
  truth_df <- global_truth
  if (length(group_truth_rows) > 0) {
    truth_df <- rbind(truth_df, do.call(rbind, group_truth_rows))
  }
  if (!is.null(cell_truth_rows) && nrow(cell_truth_rows) > 0) {
    truth_df <- rbind(truth_df, cell_truth_rows)
  }
  truth_df <- truth_df[
    truth_df$score > 0 &
      !is.na(truth_df$score) &
      truth_df$source != truth_df$target,
    ,
    drop = FALSE
  ]
  if (nrow(truth_df) == 0) {
    stop("truth/networks.csv derivation produced no nonzero edges.", call. = FALSE)
  }
  truth_df <- truth_df[order(truth_df$context, truth_df$source, truth_df$target), , drop = FALSE]
  write.csv(
    truth_df,
    file = file.path(output_dir, "truth", "networks.csv"),
    row.names = FALSE,
    quote = TRUE
  )
}

write_manifest <- function(request, params, dataset, output_dir, native_outputs = structure(list(), names = character())) {
  expression_path <- file.path(output_dir, "expression.tsv")
  expression_header <- strsplit(readLines(expression_path, n = 1L, warn = FALSE), "\t", fixed = TRUE)[[1]]
  expression_columns <- max(length(expression_header) - 1L, 0L)
  expression_genes <- ncol(dataset$counts)

  manifest <- list(
    schema_version = "1.0",
    simulator_id = request$simulator_id,
    profile = request$profile,
    seed = as.integer(request$seed),
    expression = list(
      path = "expression.tsv",
      genes = expression_genes,
      columns = expression_columns,
      column_kind = "cells",
      expression_profile = "scrna"
    ),
    extras = list(
      groups = if (file.exists(file.path(output_dir, "extras", "groups.tsv"))) "extras/groups.tsv" else NULL,
      cell_phenotypes = if (file.exists(file.path(output_dir, "extras", "cell_phenotypes.tsv"))) "extras/cell_phenotypes.tsv" else NULL,
      cluster_identities = if (file.exists(file.path(output_dir, "extras", "cluster_identities.tsv"))) "extras/cluster_identities.tsv" else NULL,
      enrichment_background = if (file.exists(file.path(output_dir, "extras", "enrichment_background.txt"))) "extras/enrichment_background.txt" else NULL,
      lineage_tree = if (file.exists(file.path(output_dir, "extras", "lineage_tree.tsv"))) "extras/lineage_tree.tsv" else NULL,
      pseudotime = if (file.exists(file.path(output_dir, "extras", "pseudotime.tsv"))) "extras/pseudotime.tsv" else NULL,
      prior_grn = if (file.exists(file.path(output_dir, "extras", "prior_grn.tsv"))) "extras/prior_grn.tsv" else NULL,
      tf_list = if (file.exists(file.path(output_dir, "extras", "tf_list.txt"))) "extras/tf_list.txt" else NULL,
      prior_grn_by_group = if (file.exists(file.path(output_dir, "extras", "prior_grn_by_group.tsv"))) "extras/prior_grn_by_group.tsv" else NULL
    ),
    native_outputs = if (length(native_outputs) > 0) native_outputs else structure(list(), names = character()),
    truth = list(
      gene_universe = "truth/gene_universe.txt",
      networks = "truth/networks.csv"
    ),
    provenance = list(
      raw_dir = "provenance/raw",
      notes = sprintf(
        "CRAN dyngen %s run from the public package API with ANDREA wrapper-derived groups/lineage extras.",
        as.character(utils::packageVersion("dyngen"))
      )
    )
  )
  write_json_atomic(file.path(output_dir, "simulator-output-manifest.json"), manifest)
}

request <- parse_request(request_path)
params <- normalise_params(request)
runtime_resources <- normalise_runtime_resources(request)
effective_extras <- unique(as.character(request$effective_extras))
native_outputs <- unique(as.character(request$native_outputs %||% character()))
cache_dir <- Sys.getenv("DYNGEN_CACHE_DIR", unset = "/opt/dyngen-cache")
dir.create(cache_dir, recursive = TRUE, showWarnings = FALSE)
options(dyngen_download_cache_dir = cache_dir, Ncpus = runtime_resources$threads)

write_json_atomic(file.path(raw_dir, "simulator-run-request.json"), request)
write_json_atomic(
  file.path(raw_dir, "wrapper_environment.json"),
  list(
    dyngen_version = as.character(utils::packageVersion("dyngen")),
    cache_dir = cache_dir,
    runtime_resources = request$runtime_resources
  )
)
writeLines(capture.output(sessionInfo()), con = file.path(raw_dir, "session_info.txt"))

write_progress("running", "validate_request", "Validating dyngen wrapper request.")

tryCatch(
  {
    set.seed(as.integer(request$seed))

    write_progress("running", "initialise_model", "Initialising dyngen model.")
    backbone <- load_backbone(params$backbone_template)
    need_pseudotime <- "pseudotime" %in% effective_extras
    need_cell_phenotypes <- "cell_phenotypes" %in% effective_extras
    need_cluster_identities <- "cluster_identities" %in% effective_extras
    need_enrichment_background <- "enrichment_background" %in% effective_extras
    need_prior_grn <- "prior_grn" %in% effective_extras
    need_prior_grn_by_group <- "prior_grn_by_group" %in% effective_extras
    need_lineage <- "lineage_tree" %in% effective_extras
    need_tf_list <- "tf_list" %in% effective_extras
    need_public_cell_truth <- identical(request$profile, "scrna_cell_specific")
    need_public_group_truth <- identical(request$profile, "scrna_grouped") || need_public_cell_truth
    need_groups <- need_public_group_truth || any(c("groups", "lineage_tree", "cell_phenotypes", "cluster_identities", "prior_grn_by_group") %in% effective_extras)
    need_regulatory_network_sc <- "regulatory_network_sc" %in% native_outputs
    need_rna_velocity <- "rna_velocity" %in% native_outputs
    need_cellwise_grn <- need_public_group_truth || need_public_cell_truth || need_lineage || need_prior_grn_by_group || need_regulatory_network_sc
    group_truth_rows <- list()
    cell_truth_rows <- NULL
    exported_native_outputs <- structure(list(), names = character())

    init <- dyngen::initialise_model(
      backbone = backbone,
      num_cells = params$num_cells,
      num_tfs = params$num_tfs %||% nrow(backbone$module_info),
      num_targets = params$num_targets,
      num_hks = params$num_hks,
      distance_metric = params$distance_metric,
      tf_network_params = build_tf_network_params(params),
      feature_network_params = build_feature_network_params(params),
      verbose = FALSE,
      num_cores = runtime_resources$threads,
      download_cache_dir = cache_dir,
      gold_standard_params = build_gold_standard_params(params),
      simulation_params = build_simulation_params(params, need_cellwise_grn, need_rna_velocity),
      experiment_params = build_experiment_params(params)
    )

    write_progress("running", "run_simulator", "Running dyngen::generate_dataset().")
    out <- dyngen::generate_dataset(
      init,
      format = "list",
      make_plots = FALSE,
      store_dimred = params$simulation_params$compute_dimred,
      store_cellwise_grn = need_cellwise_grn,
      store_rna_velocity = isTRUE(params$simulation_params$compute_rna_velocity) || need_rna_velocity
    )
    dataset <- out$dataset
    model <- out$model

    saveRDS(model, file.path(raw_dir, "model.rds"), compress = TRUE)
    saveRDS(dataset, file.path(raw_dir, "dataset.rds"), compress = TRUE)

    write_progress("running", "package_outputs", "Writing normalized ANDREA outputs.")
    write_expression_tsv(dataset$counts, file.path(output_dir, "expression.tsv"))
    write_gene_universe(dataset$counts, file.path(output_dir, "truth", "gene_universe.txt"))
    global_truth <- derive_global_truth(model)
    exported_native_outputs <- write_native_outputs(dataset, native_outputs, output_dir)
    if (need_public_cell_truth) {
      write_progress("running", "derive_truth", "Deriving cell truth from dyngen cell-specific GRN outputs.")
      cell_truth_rows <- derive_cell_truth(
        dataset = dataset,
        cell_ids = rownames(dataset$counts),
        genes = colnames(dataset$counts),
        raw_dir = raw_dir
      )
    }

    if (need_tf_list) {
      write_tf_list(model$feature_info, file.path(output_dir, "extras", "tf_list.txt"))
    }
    if (need_enrichment_background) {
      write_enrichment_background(dataset$counts, file.path(output_dir, "extras", "enrichment_background.txt"))
    }
    if (need_prior_grn) {
      write_prior_grn(model, file.path(output_dir, "extras", "prior_grn.tsv"))
    }
    if (need_pseudotime) {
      milestone_order <- derive_milestone_order(
        dataset$milestone_network,
        milestone_ids = unique(as.character(dataset$milestone_percentages$milestone_id))
      )
      write_pseudotime(
        dataset$milestone_percentages,
        dataset$cell_ids,
        milestone_order,
        file.path(output_dir, "extras", "pseudotime.tsv")
      )
    }

    if (need_groups) {
      groups_df <- derive_groups(dataset$milestone_percentages, dataset$cell_ids)
      milestone_order <- derive_milestone_order(
        dataset$milestone_network,
        milestone_ids = unique(as.character(groups_df$cluster))
      )
      write_groups(groups_df, file.path(output_dir, "extras", "groups.tsv"))
      group_truth_result <- NULL
      if (need_public_group_truth || need_prior_grn_by_group || need_lineage) {
        write_progress("running", "derive_truth", "Deriving group truth from dyngen cell-specific GRN outputs.")
        group_truth_result <- derive_group_truth(
          dataset = dataset,
          groups_df = groups_df,
          raw_dir = raw_dir
        )
        if (need_public_group_truth) {
          group_truth_rows <- group_truth_result$group_truth_rows
        }
      }
      if (need_cell_phenotypes) {
        write_cell_phenotypes(groups_df, milestone_order, file.path(output_dir, "extras", "cell_phenotypes.tsv"))
      }
      if (need_cluster_identities) {
        write_cluster_identities(groups_df, milestone_order, file.path(output_dir, "extras", "cluster_identities.tsv"))
      }
      if (need_prior_grn_by_group) {
        write_prior_grn_by_group(
          group_truth_result$group_edge_activity,
          file.path(output_dir, "extras", "prior_grn_by_group.tsv")
        )
      }
      if (need_lineage) {
        write_progress("running", "derive_extras", "Deriving lineage_tree from dyngen milestone transitions and group truth.")
        derive_lineage_tree(
          dataset = dataset,
          groups_df = groups_df,
          active_edges_by_group = group_truth_result$active_edges_by_group,
          path = file.path(output_dir, "extras", "lineage_tree.tsv"),
          raw_dir = raw_dir
        )
      }
    }

    write_truth_networks(global_truth, group_truth_rows, cell_truth_rows, output_dir)

    write_progress("running", "write_manifest", "Writing simulator-output-manifest.json.")
    write_manifest(
      request,
      params,
      dataset,
      output_dir,
      native_outputs = exported_native_outputs
    )
    write_progress("done", "done", "dyngen wrapper completed successfully.")
  },
  error = function(exc) {
    write_progress("failed", "failed", conditionMessage(exc))
    stop(exc)
  }
)
