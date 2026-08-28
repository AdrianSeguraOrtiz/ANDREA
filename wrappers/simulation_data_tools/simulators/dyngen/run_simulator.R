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
  required_fields <- c("simulator_id", "data_axes", "truth_requirements", "seed", "effective_extras", "params", "runtime_resources")
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

truth_contexts <- function(req) {
  contexts <- req$truth_requirements$contexts %||% character()
  contexts <- as.character(contexts)
  if (!("global" %in% contexts)) {
    stop("truth_requirements.contexts must include global.", call. = FALSE)
  }
  unsupported <- setdiff(contexts, c("global", "group", "column"))
  if (length(unsupported) > 0) {
    stop(
      sprintf("dyngen wrapper does not support truth context(s): %s", paste(unsupported, collapse = ", ")),
      call. = FALSE
    )
  }
  contexts
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
    ),
    knockdown_params = list(
      num_genes = as.integer((params$knockdown_params$num_genes) %||% 1L),
      multiplier = as.numeric((params$knockdown_params$multiplier) %||% 0.0),
      timepoint = as.numeric((params$knockdown_params$timepoint) %||% 0.5)
    )
  )
}

validate_semantic_request <- function(req, params, effective_extras) {
  if (!("tf_list" %in% effective_extras)) {
    stop("effective_extras must include required extra tf_list.", call. = FALSE)
  }
  axes <- req$data_axes
  if (!identical(as.character(axes$measurement), "rna_expression")) {
    stop("dyngen wrapper only supports data_axes.measurement=rna_expression.", call. = FALSE)
  }
  if (!identical(as.character(axes$resolution), "single_cell")) {
    stop("dyngen wrapper only supports data_axes.resolution=single_cell.", call. = FALSE)
  }
  if (!identical(as.character(axes$column_kind), "cells")) {
    stop("dyngen wrapper only supports data_axes.column_kind=cells.", call. = FALSE)
  }

  design <- as.character(axes$experimental_design)
  has_timepoints <- "timepoints" %in% effective_extras
  has_perturbation_design <- "perturbation_design" %in% effective_extras
  has_interventions <- "interventions" %in% effective_extras

  if (!identical(design, "time_series") && has_timepoints) {
    stop("extras/timepoints.tsv is only supported for dyngen time_series capabilities.", call. = FALSE)
  }
  if (!identical(design, "perturbational") && (has_perturbation_design || has_interventions)) {
    stop(
      "extras/perturbation_design.tsv and extras/interventions.tsv are only supported for dyngen perturbational capabilities.",
      call. = FALSE
    )
  }

  if (identical(design, "trajectory")) {
    if (!identical(params$experiment_params$kind, "snapshot")) {
      stop("dyngen trajectory capabilities require experiment_params.kind=snapshot.", call. = FALSE)
    }
    if (params$simulation_params$num_knockdown_simulations != 0L) {
      stop("dyngen trajectory capabilities require simulation_params.num_knockdown_simulations=0.", call. = FALSE)
    }
    return(invisible(TRUE))
  }

  if (identical(design, "time_series")) {
    if (!identical(params$experiment_params$kind, "synchronised")) {
      stop("dyngen time_series capabilities require experiment_params.kind=synchronised.", call. = FALSE)
    }
    if (params$simulation_params$num_knockdown_simulations != 0L) {
      stop("dyngen time_series capabilities require simulation_params.num_knockdown_simulations=0.", call. = FALSE)
    }
    if (!has_timepoints) {
      stop("dyngen time_series capabilities require effective_extras to include timepoints.", call. = FALSE)
    }
    return(invisible(TRUE))
  }

  if (identical(design, "perturbational")) {
    if (!identical(params$experiment_params$kind, "snapshot")) {
      stop("dyngen perturbational capabilities require experiment_params.kind=snapshot.", call. = FALSE)
    }
    if (params$simulation_params$num_knockdown_simulations < 1L) {
      stop(
        "dyngen perturbational capabilities require simulation_params.num_knockdown_simulations >= 1.",
        call. = FALSE
      )
    }
    if (params$knockdown_params$num_genes != 1L) {
      stop("dyngen perturbational capabilities require knockdown_params.num_genes=1.", call. = FALSE)
    }
    if (!has_perturbation_design || !has_interventions) {
      stop(
        "dyngen perturbational capabilities require effective_extras to include perturbation_design and interventions.",
        call. = FALSE
      )
    }
    return(invisible(TRUE))
  }

  stop(sprintf("dyngen wrapper does not support experimental_design=%s.", design), call. = FALSE)
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
  knockdown_count <- params$simulation_params$num_knockdown_simulations
  dplyr::bind_rows(
    dyngen::simulation_type_wild_type(
      num_simulations = params$simulation_params$num_simulations
    ),
    dyngen::simulation_type_knockdown(
      num_simulations = knockdown_count,
      timepoint = rep(params$knockdown_params$timepoint, knockdown_count),
      genes = "*",
      num_genes = rep(params$knockdown_params$num_genes, knockdown_count),
      multiplier = rep(params$knockdown_params$multiplier, knockdown_count)
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
  names(groups_df)[names(groups_df) == "cell"] <- "column"
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
    column = as.character(cell_ids),
    pseudotime = as.numeric(pseudotime),
    stringsAsFactors = FALSE
  )
  write.table(out, file = path, sep = "\t", row.names = FALSE, col.names = TRUE, quote = FALSE)
}

write_column_phenotypes <- function(groups_df, milestone_order, path) {
  phenotype_order <- as.integer(milestone_order[as.character(groups_df$cluster)])
  if (any(is.na(phenotype_order))) {
    fallback_order <- if (length(milestone_order) == 0) 0L else max(as.integer(milestone_order), na.rm = TRUE) + 1L
    phenotype_order[is.na(phenotype_order)] <- fallback_order
  }
  out <- data.frame(
    column = groups_df$cell,
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

write_timepoints <- function(cell_info, cell_ids, path) {
  cell_df <- as.data.frame(cell_info, stringsAsFactors = FALSE)
  if (!("cell_id" %in% names(cell_df))) {
    stop("timepoints derivation requires dataset$cell_info$cell_id.", call. = FALSE)
  }
  if (!("timepoint_group" %in% names(cell_df))) {
    stop("timepoints derivation requires dyngen synchronised experiment timepoint_group metadata.", call. = FALSE)
  }

  cell_df$cell_id <- as.character(cell_df$cell_id)
  cell_df$timepoint_group <- as.numeric(cell_df$timepoint_group)
  timepoint <- cell_df$timepoint_group[match(cell_ids, cell_df$cell_id)]
  if (any(is.na(timepoint))) {
    stop("timepoints derivation found expression columns without timepoint_group metadata.", call. = FALSE)
  }

  out <- data.frame(
    column = as.character(cell_ids),
    timepoint = as.numeric(timepoint),
    timepoint_label = paste0("timepoint_", as.integer(timepoint)),
    stringsAsFactors = FALSE
  )
  write.table(out, file = path, sep = "\t", row.names = FALSE, col.names = TRUE, quote = FALSE)
}

derive_knockdown_map <- function(model) {
  kd <- model$simulations$kd_multiplier
  if (is.null(kd)) {
    stop("perturbational extras require dyngen model$simulations$kd_multiplier.", call. = FALSE)
  }
  kd_df <- as.data.frame(kd, stringsAsFactors = FALSE)
  required_columns <- c("simulation_i", "gene", "multiplier")
  missing_columns <- setdiff(required_columns, names(kd_df))
  if (length(missing_columns) > 0) {
    stop(
      sprintf("dyngen kd_multiplier is missing required columns: %s", paste(missing_columns, collapse = ", ")),
      call. = FALSE
    )
  }

  kd_df$simulation_i <- as.character(kd_df$simulation_i)
  kd_df$gene <- as.character(kd_df$gene)
  kd_df$multiplier <- as.numeric(kd_df$multiplier)
  kd_df <- unique(kd_df[, required_columns, drop = FALSE])
  target_counts <- aggregate(gene ~ simulation_i, data = kd_df, FUN = function(value) length(unique(value)))
  multi_target_simulations <- target_counts$simulation_i[target_counts$gene != 1L]
  if (length(multi_target_simulations) > 0) {
    stop(
      sprintf(
        "perturbation_design requires one knocked-down target per simulation; found multiple targets for simulation_i: %s",
        paste(multi_target_simulations, collapse = ", ")
      ),
      call. = FALSE
    )
  }

  kd_df$intervention <- paste0("knockdown_", kd_df$gene, "_sim", kd_df$simulation_i)
  kd_df$effect <- "knockdown"
  kd_df$sign <- -1L
  kd_df$dose <- pmax(0, 1 - kd_df$multiplier)
  kd_df
}

write_interventions <- function(model, params, path) {
  kd_df <- derive_knockdown_map(model)
  out <- data.frame(
    intervention = kd_df$intervention,
    target = kd_df$gene,
    effect = kd_df$effect,
    sign = kd_df$sign,
    dose = kd_df$dose,
    timepoint = params$knockdown_params$timepoint,
    stringsAsFactors = FALSE
  )
  out <- out[order(out$intervention), , drop = FALSE]
  write.table(out, file = path, sep = "\t", row.names = FALSE, col.names = TRUE, quote = FALSE)
}

write_perturbation_design <- function(dataset, model, params, path) {
  cell_df <- as.data.frame(dataset$cell_info, stringsAsFactors = FALSE)
  if (!all(c("cell_id", "simulation_i") %in% names(cell_df))) {
    stop("perturbation_design derivation requires dataset$cell_info cell_id and simulation_i columns.", call. = FALSE)
  }
  kd_df <- derive_knockdown_map(model)
  names(kd_df)[names(kd_df) == "gene"] <- "target"
  kd_df <- kd_df[, c("simulation_i", "intervention", "target", "multiplier", "dose"), drop = FALSE]

  cell_df$cell_id <- as.character(cell_df$cell_id)
  cell_df$simulation_i <- as.character(cell_df$simulation_i)
  design <- merge(
    data.frame(
      column = as.character(dataset$cell_ids),
      cell_id = as.character(dataset$cell_ids),
      stringsAsFactors = FALSE
    ),
    cell_df[, c("cell_id", "simulation_i"), drop = FALSE],
    by = "cell_id",
    all.x = TRUE,
    all.y = FALSE,
    sort = FALSE
  )
  if (any(is.na(design$simulation_i))) {
    stop("perturbation_design derivation found expression columns without simulation_i metadata.", call. = FALSE)
  }

  design <- merge(design, kd_df, by = "simulation_i", all.x = TRUE, all.y = FALSE, sort = FALSE)
  is_control <- is.na(design$intervention)
  design$condition <- ifelse(is_control, "control", design$intervention)
  design$perturbation <- ifelse(is_control, "none", "knockdown")
  design$target[is_control] <- ""
  design$dose[is_control] <- 0
  design$timepoint <- ifelse(is_control, 0, params$knockdown_params$timepoint)
  design$replicate <- paste0("simulation_", design$simulation_i)
  design$control <- is_control
  design$intervention[is_control] <- ""

  out <- design[
    match(as.character(dataset$cell_ids), design$column),
    c("column", "condition", "perturbation", "target", "dose", "timepoint", "replicate", "control", "intervention"),
    drop = FALSE
  ]
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
  root_groups <- setdiff(used_groups, lineage_tree$child)
  if (length(root_groups) > 0) {
    root_rows <- data.frame(
      child = root_groups,
      parent = "__root__",
      gain_rate = 0,
      loss_rate = 0,
      stringsAsFactors = FALSE
    )
    lineage_tree <- rbind(root_rows, lineage_tree)
  }

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

derive_column_truth <- function(dataset, cell_ids, genes, raw_dir) {
  if (is.null(dataset$regulatory_network_sc)) {
    stop("column truth derivation requires dyngen cell-specific GRN output.", call. = FALSE)
  }

  regulatory_network_sc <- as.data.frame(dataset$regulatory_network_sc, stringsAsFactors = FALSE)
  required_columns <- c("cell_id", "regulator", "target", "strength")
  missing_columns <- setdiff(required_columns, names(regulatory_network_sc))
  if (length(missing_columns) > 0) {
    stop(
      sprintf(
        "dyngen regulatory_network_sc is missing required columns for column truth: %s",
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

  column_truth <- regulatory_network_sc[
    !is.na(regulatory_network_sc$strength) &
      regulatory_network_sc$strength != 0 &
      regulatory_network_sc$regulator != regulatory_network_sc$target,
    ,
    drop = FALSE
  ]
  column_truth <- data.frame(
    source = as.character(column_truth$regulator),
    target = as.character(column_truth$target),
    score = abs(as.numeric(column_truth$strength)),
    sign = ifelse(column_truth$strength > 0, "+", "-"),
    evidence = "simulated_truth",
    context = paste0("column:", column_truth$cell_id),
    stringsAsFactors = FALSE
  )
  column_truth <- column_truth[column_truth$score > 0 & !is.na(column_truth$score), , drop = FALSE]
  if (nrow(column_truth) == 0) {
    stop("column truth derivation produced no nonzero cell-specific edges.", call. = FALSE)
  }
  column_truth <- column_truth[order(column_truth$context, column_truth$source, column_truth$target), , drop = FALSE]

  context_counts <- as.data.frame(table(column_truth$context), stringsAsFactors = FALSE)
  names(context_counts) <- c("context", "edge_count")
  context_counts$column <- sub("^column:", "", context_counts$context)
  context_counts <- context_counts[, c("column", "context", "edge_count"), drop = FALSE]
  write.table(
    context_counts,
    file = file.path(raw_dir, "column_networks_index.tsv"),
    sep = "\t",
    row.names = FALSE,
    col.names = TRUE,
    quote = FALSE
  )

  column_truth
}

write_truth_networks <- function(global_truth, group_truth_rows, column_truth_rows, output_dir) {
  truth_df <- global_truth
  if (length(group_truth_rows) > 0) {
    truth_df <- rbind(truth_df, do.call(rbind, group_truth_rows))
  }
  if (!is.null(column_truth_rows) && nrow(column_truth_rows) > 0) {
    truth_df <- rbind(truth_df, column_truth_rows)
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
  manifest_truth_requirements <- list(
    contexts = I(as.character(request$truth_requirements$contexts %||% character()))
  )

  manifest <- list(
    schema_version = "1.0",
    simulator_id = request$simulator_id,
    data_axes = request$data_axes,
    truth_requirements = manifest_truth_requirements,
    seed = as.integer(request$seed),
    expression = list(
      path = "expression.tsv",
      genes = expression_genes,
      columns = expression_columns,
      column_kind = "cells"
    ),
    extras = list(
      groups = if (file.exists(file.path(output_dir, "extras", "groups.tsv"))) "extras/groups.tsv" else NULL,
      column_phenotypes = if (file.exists(file.path(output_dir, "extras", "column_phenotypes.tsv"))) "extras/column_phenotypes.tsv" else NULL,
      cluster_identities = if (file.exists(file.path(output_dir, "extras", "cluster_identities.tsv"))) "extras/cluster_identities.tsv" else NULL,
      enrichment_background = if (file.exists(file.path(output_dir, "extras", "enrichment_background.txt"))) "extras/enrichment_background.txt" else NULL,
      lineage_tree = if (file.exists(file.path(output_dir, "extras", "lineage_tree.tsv"))) "extras/lineage_tree.tsv" else NULL,
      timepoints = if (file.exists(file.path(output_dir, "extras", "timepoints.tsv"))) "extras/timepoints.tsv" else NULL,
      perturbation_design = if (file.exists(file.path(output_dir, "extras", "perturbation_design.tsv"))) "extras/perturbation_design.tsv" else NULL,
      interventions = if (file.exists(file.path(output_dir, "extras", "interventions.tsv"))) "extras/interventions.tsv" else NULL,
      pseudotime = if (file.exists(file.path(output_dir, "extras", "pseudotime.tsv"))) "extras/pseudotime.tsv" else NULL,
      prior_grn = if (file.exists(file.path(output_dir, "extras", "prior_grn.tsv"))) "extras/prior_grn.tsv" else NULL,
      tf_list = "extras/tf_list.txt",
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
    validate_semantic_request(request, params, effective_extras)

    write_progress("running", "initialise_model", "Initialising dyngen model.")
    backbone <- load_backbone(params$backbone_template)
    need_pseudotime <- "pseudotime" %in% effective_extras
    need_column_phenotypes <- "column_phenotypes" %in% effective_extras
    need_cluster_identities <- "cluster_identities" %in% effective_extras
    need_enrichment_background <- "enrichment_background" %in% effective_extras
    need_prior_grn <- "prior_grn" %in% effective_extras
    need_prior_grn_by_group <- "prior_grn_by_group" %in% effective_extras
    need_lineage <- "lineage_tree" %in% effective_extras
    need_timepoints <- "timepoints" %in% effective_extras
    need_perturbation_design <- "perturbation_design" %in% effective_extras
    need_interventions <- "interventions" %in% effective_extras
    requested_truth_contexts <- truth_contexts(request)
    need_public_column_truth <- "column" %in% requested_truth_contexts
    need_public_group_truth <- ("group" %in% requested_truth_contexts) || need_public_column_truth
    need_groups <- need_public_group_truth || any(c("groups", "lineage_tree", "column_phenotypes", "cluster_identities", "prior_grn_by_group") %in% effective_extras)
    need_regulatory_network_sc <- "regulatory_network_sc" %in% native_outputs
    need_rna_velocity <- "rna_velocity" %in% native_outputs
    need_cellwise_grn <- need_public_group_truth || need_public_column_truth || need_lineage || need_prior_grn_by_group || need_regulatory_network_sc
    group_truth_rows <- list()
    column_truth_rows <- NULL
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
    if (need_public_column_truth) {
      write_progress("running", "derive_truth", "Deriving column truth from dyngen cell-specific GRN outputs.")
      column_truth_rows <- derive_column_truth(
        dataset = dataset,
        cell_ids = rownames(dataset$counts),
        genes = colnames(dataset$counts),
        raw_dir = raw_dir
      )
    }

    write_tf_list(model$feature_info, file.path(output_dir, "extras", "tf_list.txt"))
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
    if (need_timepoints) {
      write_timepoints(
        dataset$cell_info,
        dataset$cell_ids,
        file.path(output_dir, "extras", "timepoints.tsv")
      )
    }
    if (need_interventions) {
      write_interventions(
        model,
        params,
        file.path(output_dir, "extras", "interventions.tsv")
      )
    }
    if (need_perturbation_design) {
      write_perturbation_design(
        dataset,
        model,
        params,
        file.path(output_dir, "extras", "perturbation_design.tsv")
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
      if (need_column_phenotypes) {
        write_column_phenotypes(groups_df, milestone_order, file.path(output_dir, "extras", "column_phenotypes.tsv"))
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

    write_truth_networks(global_truth, group_truth_rows, column_truth_rows, output_dir)

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
