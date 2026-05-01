#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  suppressWarnings(library(jsonlite))
  suppressWarnings(library(inferCSN))
})

`%||%` <- function(x, y) {
  if (is.null(x)) y else x
}

parse_args <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  out <- list()
  i <- 1L
  while (i <= length(args)) {
    key <- args[[i]]
    if (!startsWith(key, "--")) {
      stop(sprintf("Unknown argument: %s", key), call. = FALSE)
    }
    if (i == length(args)) {
      stop(sprintf("Missing value for %s", key), call. = FALSE)
    }
    value <- args[[i + 1L]]
    if (startsWith(value, "--")) {
      stop(sprintf("Missing value for %s", key), call. = FALSE)
    }
    out[[substring(key, 3L)]] <- value
    i <- i + 2L
  }
  out
}

write_progress <- function(progress_path, status, percent, phase, message,
                           completed = NULL, total = NULL, error = NULL) {
  payload <- list(
    status = status,
    phase = phase,
    percent = max(0L, min(100L, as.integer(percent))),
    message = message,
    timestamp = as.numeric(Sys.time())
  )
  if (!is.null(completed)) payload$completed <- as.integer(completed)
  if (!is.null(total)) payload$total <- as.integer(total)
  if (!is.null(error)) payload$error <- as.character(error)

  tmp_path <- paste0(progress_path, ".tmp")
  writeLines(toJSON(payload, auto_unbox = TRUE, null = "null"), tmp_path, useBytes = TRUE)
  if (!file.rename(tmp_path, progress_path)) {
    stop("Failed to write progress.json atomically.", call. = FALSE)
  }
}

append_log <- function(log_path, message) {
  cat(paste0(format(Sys.time(), "%Y-%m-%d %H:%M:%S"), " ", message, "\n"),
      file = log_path, append = TRUE)
}

is_scalar_logical <- function(x) is.logical(x) && length(x) == 1L && !is.na(x)
is_scalar_number <- function(x) is.numeric(x) && length(x) == 1L && !is.na(x)
is_scalar_string <- function(x) is.character(x) && length(x) == 1L && !is.na(x)

as_int_checked <- function(name, value, min_value = NULL) {
  if (!is_scalar_number(value) || abs(value - round(value)) > 1e-9) {
    stop(sprintf("%s must be an integer.", name), call. = FALSE)
  }
  out <- as.integer(round(value))
  if (!is.null(min_value) && out < min_value) {
    stop(sprintf("%s must be >= %d.", name, as.integer(min_value)), call. = FALSE)
  }
  out
}

as_float_checked <- function(name, value, min_value = NULL, max_value = NULL,
                             exclusive_min = FALSE) {
  if (!is_scalar_number(value)) {
    stop(sprintf("%s must be numeric.", name), call. = FALSE)
  }
  out <- as.numeric(value)
  if (!is.finite(out)) {
    stop(sprintf("%s must be finite.", name), call. = FALSE)
  }
  if (!is.null(min_value)) {
    if (exclusive_min && out <= min_value) {
      stop(sprintf("%s must be > %s.", name, min_value), call. = FALSE)
    }
    if (!exclusive_min && out < min_value) {
      stop(sprintf("%s must be >= %s.", name, min_value), call. = FALSE)
    }
  }
  if (!is.null(max_value) && out > max_value) {
    stop(sprintf("%s must be <= %s.", name, max_value), call. = FALSE)
  }
  out
}

load_params <- function(params_path) {
  params <- fromJSON(params_path, simplifyVector = TRUE)
  if (!is.list(params)) {
    stop("params.json must be a JSON object.", call. = FALSE)
  }
  params
}

resolve_params <- function(raw_params) {
  expected <- c(
    "penalty",
    "cross_validation",
    "seed",
    "n_folds",
    "subsampling_method",
    "subsampling_ratio",
    "r_squared_threshold",
    "sift_method",
    "entropy_method",
    "effective_entropy",
    "shuffles",
    "entropy_nboot",
    "lag_value",
    "entropy_p_value"
  )
  missing <- setdiff(expected, names(raw_params))
  if (length(missing) > 0L) {
    stop(
      sprintf("Missing required params in params.json: %s", paste(missing, collapse = ", ")),
      call. = FALSE
    )
  }

  unknown <- setdiff(names(raw_params), expected)
  if (length(unknown) > 0L) {
    warning(sprintf("Unknown params ignored: %s", paste(sort(unknown), collapse = ", ")))
  }

  penalty <- raw_params$penalty
  if (!is_scalar_string(penalty) || !(penalty %in% c("L0", "L0L1", "L0L2"))) {
    stop("penalty must be one of: L0, L0L1, L0L2.", call. = FALSE)
  }

  cross_validation <- raw_params$cross_validation
  if (!is_scalar_logical(cross_validation)) {
    stop("cross_validation must be a boolean.", call. = FALSE)
  }

  subsampling_method <- raw_params$subsampling_method
  if (!is_scalar_string(subsampling_method) ||
      !(subsampling_method %in% c("sample", "meta_cells", "pseudobulk"))) {
    stop("subsampling_method must be one of: sample, meta_cells, pseudobulk.", call. = FALSE)
  }

  sift_method <- raw_params$sift_method
  if (!is_scalar_string(sift_method) || !(sift_method %in% c("none", "max", "entropy"))) {
    stop("sift_method must be one of: none, max, entropy.", call. = FALSE)
  }

  entropy_method <- raw_params$entropy_method
  if (!is_scalar_string(entropy_method) || !(entropy_method %in% c("Shannon", "Renyi"))) {
    stop("entropy_method must be one of: Shannon, Renyi.", call. = FALSE)
  }

  effective_entropy <- raw_params$effective_entropy
  if (!is_scalar_logical(effective_entropy)) {
    stop("effective_entropy must be a boolean.", call. = FALSE)
  }

  list(
    penalty = penalty,
    cross_validation = cross_validation,
    seed = as_int_checked("seed", raw_params$seed),
    n_folds = as_int_checked("n_folds", raw_params$n_folds, min_value = 2L),
    subsampling_method = subsampling_method,
    subsampling_ratio = as_float_checked(
      "subsampling_ratio", raw_params$subsampling_ratio,
      min_value = 0, max_value = 1, exclusive_min = TRUE
    ),
    r_squared_threshold = as_float_checked(
      "r_squared_threshold", raw_params$r_squared_threshold,
      min_value = 0, max_value = 1
    ),
    sift_method = sift_method,
    entropy_method = entropy_method,
    effective_entropy = effective_entropy,
    shuffles = as_int_checked("shuffles", raw_params$shuffles, min_value = 0L),
    entropy_nboot = as_int_checked("entropy_nboot", raw_params$entropy_nboot, min_value = 0L),
    lag_value = as_int_checked("lag_value", raw_params$lag_value, min_value = 1L),
    entropy_p_value = as_float_checked(
      "entropy_p_value", raw_params$entropy_p_value,
      min_value = 0, max_value = 1
    )
  )
}

load_execution_mode <- function(params_path) {
  execution_path <- file.path(dirname(params_path), "execution.json")
  if (!file.exists(execution_path)) {
    return("group_emulated")
  }
  execution <- fromJSON(execution_path, simplifyVector = TRUE)
  if (!is.list(execution)) {
    stop("execution.json must be a JSON object.", call. = FALSE)
  }
  mode <- execution$mode %||% "group_emulated"
  if (!is_scalar_string(mode) || mode != "group_emulated") {
    stop("inferCSN supports only execution.mode=group_emulated.", call. = FALSE)
  }
  mode
}

read_expression_tsv <- function(expr_path) {
  df <- read.delim(
    expr_path,
    sep = "\t",
    header = TRUE,
    check.names = FALSE,
    stringsAsFactors = FALSE
  )
  if (ncol(df) < 2L) {
    stop("expression.tsv must have at least 2 columns: gene + >=1 cell.", call. = FALSE)
  }

  gene_col <- names(df)[1L]
  genes <- trimws(as.character(df[[gene_col]]))
  if (any(!nzchar(genes))) {
    stop("expression.tsv contains an empty gene identifier.", call. = FALSE)
  }
  if (anyDuplicated(genes)) {
    duplicated <- sort(unique(genes[duplicated(genes)]))
    stop(sprintf("expression.tsv contains duplicated genes: %s", paste(duplicated, collapse = ", ")),
         call. = FALSE)
  }

  cells <- trimws(names(df)[-1L])
  if (any(!nzchar(cells))) {
    stop("expression.tsv contains an empty cell identifier in the header.", call. = FALSE)
  }
  if (anyDuplicated(cells)) {
    duplicated <- sort(unique(cells[duplicated(cells)]))
    stop(sprintf("expression.tsv contains duplicated cell identifiers: %s", paste(duplicated, collapse = ", ")),
         call. = FALSE)
  }

  num_df <- data.frame(
    lapply(df[, -1L, drop = FALSE], function(x) as.numeric(x)),
    check.names = FALSE
  )
  mat <- as.matrix(num_df)
  if (anyNA(mat) || any(!is.finite(mat))) {
    stop("expression.tsv contains non-finite or non-numeric expression values.", call. = FALSE)
  }

  rownames(mat) <- genes
  colnames(mat) <- cells
  cells_x_genes <- t(mat)
  rownames(cells_x_genes) <- cells
  colnames(cells_x_genes) <- genes
  cells_x_genes
}

load_tf_list <- function(extra_dir, genes) {
  tf_path <- file.path(extra_dir, "tf_list.txt")
  if (!file.exists(tf_path)) {
    return(NULL)
  }

  raw <- readLines(tf_path, warn = FALSE)
  tfs <- trimws(raw)
  tfs <- tfs[nzchar(tfs) & !startsWith(tfs, "#")]
  if (length(tfs) == 0L) {
    stop("tf_list.txt does not contain any regulators.", call. = FALSE)
  }
  if (anyDuplicated(tfs)) {
    duplicated <- sort(unique(tfs[duplicated(tfs)]))
    stop(sprintf("tf_list.txt contains duplicated TFs: %s", paste(duplicated, collapse = ", ")),
         call. = FALSE)
  }

  missing <- setdiff(tfs, genes)
  if (length(missing) > 0L) {
    stop(sprintf("tf_list.txt contains genes not present in expression.tsv: %s",
                 paste(sort(missing), collapse = ", ")), call. = FALSE)
  }
  if (length(tfs) < 2L) {
    stop("tf_list.txt must contain at least 2 regulators for inferCSN.", call. = FALSE)
  }
  tfs
}

require_groups_for_emulated_run <- function(extra_dir) {
  groups_path <- file.path(extra_dir, "groups.tsv")
  if (!file.exists(groups_path)) {
    stop("groups.tsv is required for inferCSN execution.mode=group_emulated.", call. = FALSE)
  }
}

load_pseudotime <- function(extra_dir, cells) {
  path <- file.path(extra_dir, "pseudotime.tsv")
  if (!file.exists(path)) {
    stop("pseudotime.tsv is required when sift_method=entropy.", call. = FALSE)
  }
  df <- read.delim(path, sep = "\t", header = TRUE, check.names = FALSE, stringsAsFactors = FALSE)
  if (ncol(df) < 2L) {
    stop("pseudotime.tsv must have at least 2 columns: cell + pseudotime.", call. = FALSE)
  }
  first_col <- names(df)[1L]
  if (!("pseudotime" %in% names(df))) {
    stop("pseudotime.tsv is missing required column: pseudotime.", call. = FALSE)
  }

  cell_ids <- trimws(as.character(df[[first_col]]))
  if (any(!nzchar(cell_ids))) {
    stop("pseudotime.tsv contains an empty cell identifier.", call. = FALSE)
  }
  if (anyDuplicated(cell_ids)) {
    duplicated <- sort(unique(cell_ids[duplicated(cell_ids)]))
    stop(sprintf("pseudotime.tsv contains duplicated cells: %s", paste(duplicated, collapse = ", ")),
         call. = FALSE)
  }

  values <- suppressWarnings(as.numeric(df[["pseudotime"]]))
  if (anyNA(values) || any(!is.finite(values))) {
    stop("pseudotime.tsv contains non-finite or non-numeric pseudotime values.", call. = FALSE)
  }

  # CRAN inferCSN 1.2.0 subsets meta_data without drop = FALSE inside network_sift().
  # Keep a second inert column so one-column pseudotime metadata remains a data frame.
  metadata <- data.frame(
    pseudotime = values,
    andrea_order = seq_along(values),
    stringsAsFactors = FALSE
  )
  rownames(metadata) <- cell_ids

  missing <- setdiff(cells, rownames(metadata))
  if (length(missing) > 0L) {
    stop(sprintf("pseudotime.tsv is missing cells present in expression.tsv: %s",
                 paste(sort(missing), collapse = ", ")), call. = FALSE)
  }

  metadata[cells, , drop = FALSE]
}

write_upstream_table <- function(table, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  table <- as.data.frame(table, stringsAsFactors = FALSE)
  write.table(table, path, sep = "\t", quote = FALSE, row.names = FALSE, na = "")
}

run_infercsn <- function(expr, regulators, params, threads) {
  infer_args <- list(
    object = expr,
    penalty = params$penalty,
    cross_validation = params$cross_validation,
    seed = params$seed,
    n_folds = params$n_folds,
    subsampling_method = params$subsampling_method,
    subsampling_ratio = params$subsampling_ratio,
    r_squared_threshold = params$r_squared_threshold,
    cores = threads,
    verbose = TRUE
  )
  if (!is.null(regulators)) {
    infer_args$regulators <- regulators
  }
  do.call(inferCSN::inferCSN, infer_args)
}

apply_network_sift <- function(network_table, expr, params, extra_dir, threads) {
  if (params$sift_method == "none") {
    return(network_table)
  }

  if (params$sift_method == "entropy") {
    metadata <- load_pseudotime(extra_dir, rownames(expr))
    return(network_sift_entropy_fixed(network_table, expr, metadata, params, threads))
  }

  sift_args <- list(
    network_table = network_table,
    method = params$sift_method,
    cores = threads,
    verbose = TRUE
  )

  do.call(inferCSN::network_sift, sift_args)
}

network_sift_entropy_fixed <- function(network_table, expr, metadata, params, threads) {
  samples <- intersect(rownames(metadata), rownames(expr))
  if (length(samples) == 0L) {
    stop("pseudotime.tsv and expression.tsv do not share any cells.", call. = FALSE)
  }

  metadata <- metadata[samples, , drop = FALSE]
  metadata <- metadata[order(metadata[, "pseudotime"], decreasing = FALSE), , drop = FALSE]

  network_df <- as.data.frame(network_table, stringsAsFactors = FALSE)
  genes <- unique(c(as.character(network_df$regulator), as.character(network_df$target)))
  missing_genes <- setdiff(genes, colnames(expr))
  if (length(missing_genes) > 0L) {
    stop(sprintf("Inferred network contains genes missing from expression.tsv: %s",
                 paste(sort(missing_genes), collapse = ", ")), call. = FALSE)
  }

  expr_ordered <- expr[rownames(metadata), genes, drop = FALSE]
  unique_pairs <- utils::combn(colnames(expr_ordered), 2L, simplify = FALSE)

  shuffles <- params$shuffles
  if (!params$effective_entropy) {
    shuffles <- 0L
  } else if (shuffles <= 10L) {
    shuffles <- 10L
  }

  run_pair <- function(pair) {
    result <- suppressWarnings(
      RTransferEntropy::transfer_entropy(
        expr_ordered[, pair[[1]]],
        expr_ordered[, pair[[2]]],
        lx = params$lag_value,
        ly = params$lag_value,
        entropy = params$entropy_method,
        shuffles = shuffles,
        nboot = params$entropy_nboot,
        quiet = TRUE
      )
    )
    result <- stats::coef(result)
    if (params$effective_entropy) {
      entropy_forward <- result[1, 2]
      entropy_reverse <- result[2, 2]
    } else {
      entropy_forward <- result[1, 1]
      entropy_reverse <- result[2, 1]
    }
    data.frame(
      regulator = pair[[1]],
      target = pair[[2]],
      entropy = as.numeric(entropy_forward),
      entropy_contrary = as.numeric(entropy_reverse),
      P_value = as.numeric(result[1, 4]),
      P_value_contrary = as.numeric(result[2, 4]),
      stringsAsFactors = FALSE
    )
  }

  entropy_rows <- if (threads > 1L && length(unique_pairs) > 1L) {
    parallel::mclapply(unique_pairs, run_pair, mc.cores = threads)
  } else {
    lapply(unique_pairs, run_pair)
  }
  transfer_entropy_table <- do.call(rbind, entropy_rows)

  if (params$entropy_nboot > 1L) {
    transfer_entropy_table <- transfer_entropy_table[
      transfer_entropy_table$P_value <= params$entropy_p_value &
        transfer_entropy_table$P_value_contrary <= params$entropy_p_value,
      ,
      drop = FALSE
    ]
  }
  if (!nrow(transfer_entropy_table)) {
    return(network_df[FALSE, c("regulator", "target", "weight"), drop = FALSE])
  }

  entropy_forward <- data.frame(
    regulator = transfer_entropy_table$regulator,
    target = transfer_entropy_table$target,
    weight = transfer_entropy_table$entropy,
    stringsAsFactors = FALSE
  )
  entropy_reverse <- data.frame(
    regulator = transfer_entropy_table$target,
    target = transfer_entropy_table$regulator,
    weight = transfer_entropy_table$entropy_contrary,
    stringsAsFactors = FALSE
  )
  entropy_directions <- rbind(entropy_forward, entropy_reverse)
  entropy_directions <- inferCSN::weight_sift(entropy_directions)

  kept_directions <- unique(entropy_directions[, c("regulator", "target"), drop = FALSE])
  filtered <- merge(network_df, kept_directions, by = c("regulator", "target"))
  filtered[, c("regulator", "target", "weight"), drop = FALSE]
}

network_to_andrea <- function(network_table) {
  table <- as.data.frame(network_table, stringsAsFactors = FALSE)
  required <- c("regulator", "target", "weight")
  missing <- setdiff(required, names(table))
  if (length(missing) > 0L) {
    stop(sprintf("inferCSN output is missing required columns: %s", paste(missing, collapse = ", ")),
         call. = FALSE)
  }

  if (!nrow(table)) {
    stop("inferCSN produced no interactions.", call. = FALSE)
  }

  weights <- suppressWarnings(as.numeric(table$weight))
  keep <- is.finite(weights) & weights != 0
  table <- table[keep, , drop = FALSE]
  weights <- weights[keep]
  if (!nrow(table)) {
    stop("inferCSN produced no non-zero interactions.", call. = FALSE)
  }

  out <- data.frame(
    source = as.character(table$regulator),
    target = as.character(table$target),
    score = weights,
    sign = ifelse(weights > 0, "+", "-"),
    evidence = "association",
    context = "global",
    stringsAsFactors = FALSE
  )
  out <- out[out$source != out$target, , drop = FALSE]
  if (!nrow(out)) {
    stop("inferCSN produced no non-self interactions.", call. = FALSE)
  }

  out[order(abs(out$score), decreasing = TRUE), , drop = FALSE]
}

main <- function() {
  args <- parse_args()
  input_path <- args$input %||% stop("Missing required argument: --input", call. = FALSE)
  params_path <- args$params %||% stop("Missing required argument: --params", call. = FALSE)
  extra_dir <- args$extra %||% stop("Missing required argument: --extra", call. = FALSE)
  output_dir <- args$`output-dir` %||% stop("Missing required argument: --output-dir", call. = FALSE)
  threads_raw <- args$threads %||% stop("Missing required argument: --threads", call. = FALSE)

  threads <- suppressWarnings(as.integer(threads_raw))
  if (is.na(threads) || threads <= 0L) {
    stop("--threads must be a positive integer.", call. = FALSE)
  }

  if (!file.exists(input_path)) stop(sprintf("Input file not found: %s", input_path), call. = FALSE)
  if (!file.exists(params_path)) stop(sprintf("Params file not found: %s", params_path), call. = FALSE)
  if (!dir.exists(extra_dir)) stop(sprintf("Extra directory not found: %s", extra_dir), call. = FALSE)

  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
  raw_dir <- file.path(output_dir, "raw")
  dir.create(raw_dir, recursive = TRUE, showWarnings = FALSE)
  progress_path <- file.path(output_dir, "progress.json")
  log_path <- file.path(output_dir, "infercsn.log")

  write_progress(progress_path, "running", 0L, "init", "Initializing inferCSN wrapper")
  append_log(log_path, "Initializing inferCSN wrapper")

  tryCatch({
    params <- resolve_params(load_params(params_path))
    mode <- load_execution_mode(params_path)
    require_groups_for_emulated_run(extra_dir)
    append_log(log_path, sprintf("Execution mode: %s", mode))
    append_log(log_path, sprintf("sift_method: %s", params$sift_method))

    write_progress(progress_path, "running", 10L, "load_input", "Loading expression and extra inputs")
    expr <- read_expression_tsv(input_path)
    regulators <- load_tf_list(extra_dir, colnames(expr))
    append_log(log_path, sprintf("Loaded expression matrix: %d cells x %d genes", nrow(expr), ncol(expr)))
    append_log(log_path, sprintf("Regulators: %s", if (is.null(regulators)) "all genes" else length(regulators)))

    write_progress(progress_path, "running", 30L, "inference", "Running inferCSN sparse regression")
    inferred <- run_infercsn(expr, regulators, params, threads)
    write_upstream_table(inferred, file.path(raw_dir, "infercsn_inferred_network.tsv"))
    append_log(log_path, sprintf("inferCSN returned %d raw rows", nrow(as.data.frame(inferred))))

    if (params$sift_method != "none") {
      write_progress(progress_path, "running", 75L, "sift", "Running inferCSN network_sift")
    }
    final_network <- apply_network_sift(inferred, expr, params, extra_dir, threads)
    write_upstream_table(final_network, file.path(raw_dir, "infercsn_network.tsv"))
    append_log(log_path, sprintf("Final upstream table has %d rows", nrow(as.data.frame(final_network))))

    write_progress(progress_path, "running", 92L, "write_output", "Writing network.csv")
    network_df <- network_to_andrea(final_network)
    write.csv(network_df, file.path(output_dir, "network.csv"), row.names = FALSE)
    append_log(log_path, sprintf("Wrote network.csv with %d non-zero edges", nrow(network_df)))

    write_progress(
      progress_path,
      "completed",
      100L,
      "done",
      "Inference finished",
      completed = nrow(network_df),
      total = nrow(network_df)
    )
  }, error = function(exc) {
    append_log(log_path, sprintf("ERROR: %s", conditionMessage(exc)))
    write_progress(
      progress_path,
      "failed",
      100L,
      "failed",
      "Inference failed",
      error = conditionMessage(exc)
    )
    stop(exc)
  })
}

main()
