#!/usr/bin/env Rscript

Sys.setenv(
  OMP_NUM_THREADS = "1",
  OPENBLAS_NUM_THREADS = "1",
  MKL_NUM_THREADS = "1",
  BLIS_NUM_THREADS = "1",
  VECLIB_MAXIMUM_THREADS = "1"
)

suppressPackageStartupMessages({
  suppressWarnings(library(jsonlite))
  suppressWarnings(library(ppcor))
})

NETWORK_COLUMNS <- c("source", "target", "score", "sign", "evidence", "context")
SUPPORTED_MODES <- c("global", "group_emulated")

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

write_progress <- function(progress_path, status, percent, phase, message, error = NULL) {
  payload <- list(
    status = status,
    phase = phase,
    percent = max(0L, min(100L, as.integer(percent))),
    message = message,
    timestamp = as.numeric(Sys.time())
  )
  if (!is.null(error)) {
    payload$error <- as.character(error)
  }

  tmp_path <- paste0(progress_path, ".tmp")
  writeLines(toJSON(payload, auto_unbox = TRUE, null = "null"), tmp_path, useBytes = TRUE)
  if (!file.rename(tmp_path, progress_path)) {
    stop("Failed to write progress.json atomically.", call. = FALSE)
  }
}

append_log <- function(log_path, message) {
  cat(
    paste0(format(Sys.time(), "%Y-%m-%d %H:%M:%S"), " ", message, "\n"),
    file = log_path,
    append = TRUE
  )
}

is_scalar_string <- function(x) {
  is.character(x) && length(x) == 1L && !is.na(x)
}

load_json_object <- function(path, label) {
  payload <- fromJSON(path, simplifyVector = TRUE)
  if (!is.list(payload)) {
    stop(sprintf("%s must be a JSON object.", label), call. = FALSE)
  }
  payload
}

resolve_params <- function(raw_params) {
  expected <- c("method")
  unknown <- setdiff(names(raw_params), expected)
  if (length(unknown) > 0L) {
    warning(sprintf("Unknown params ignored: %s", paste(sort(unknown), collapse = ", ")))
  }

  method <- raw_params$method %||% "pearson"
  method_values <- c("pearson", "kendall", "spearman")
  if (!is_scalar_string(method) || !(method %in% method_values)) {
    stop(
      sprintf("method must be one of: %s", paste(method_values, collapse = ", ")),
      call. = FALSE
    )
  }

  list(method = method)
}

load_execution_mode <- function(params_path) {
  execution_path <- file.path(dirname(params_path), "execution.json")
  if (!file.exists(execution_path)) {
    return("global")
  }
  execution <- load_json_object(execution_path, "execution.json")
  mode <- execution$mode %||% "global"
  if (!is_scalar_string(mode)) {
    stop("execution.mode must be a string.", call. = FALSE)
  }
  if (!(mode %in% SUPPORTED_MODES)) {
    stop(
      sprintf("ppcor supports only execution.mode values: %s", paste(SUPPORTED_MODES, collapse = ", ")),
      call. = FALSE
    )
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
  if (ncol(df) < 3L) {
    stop("expression.tsv must have one gene column and at least two expression columns.", call. = FALSE)
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

  expression_columns <- trimws(names(df)[-1L])
  if (any(!nzchar(expression_columns))) {
    stop("expression.tsv contains an empty expression column identifier.", call. = FALSE)
  }
  if (anyDuplicated(expression_columns)) {
    duplicated <- sort(unique(expression_columns[duplicated(expression_columns)]))
    stop(sprintf(
      "expression.tsv contains duplicated expression columns: %s",
      paste(duplicated, collapse = ", ")
    ), call. = FALSE)
  }

  num_df <- data.frame(
    lapply(df[, -1L, drop = FALSE], function(x) suppressWarnings(as.numeric(x))),
    check.names = FALSE
  )
  mat <- as.matrix(num_df)
  if (anyNA(mat) || any(!is.finite(mat))) {
    stop("expression.tsv contains non-finite or non-numeric expression values.", call. = FALSE)
  }

  rownames(mat) <- genes
  colnames(mat) <- expression_columns
  mat
}

read_groups <- function(extra_dir, expression_columns) {
  groups_path <- file.path(extra_dir, "groups.tsv")
  if (!file.exists(groups_path)) {
    stop("groups.tsv is required when execution.mode=group_emulated.", call. = FALSE)
  }
  groups_df <- read.delim(
    groups_path,
    sep = "\t",
    header = TRUE,
    check.names = FALSE,
    stringsAsFactors = FALSE
  )
  if (ncol(groups_df) < 2L) {
    stop("groups.tsv must contain an expression-column id column and a cluster column.", call. = FALSE)
  }
  if (!("cluster" %in% names(groups_df))) {
    stop("groups.tsv is missing required column: cluster.", call. = FALSE)
  }

  column_ids <- trimws(as.character(groups_df[[1L]]))
  clusters <- trimws(as.character(groups_df[["cluster"]]))
  if (any(!nzchar(column_ids))) {
    stop("groups.tsv contains an empty expression-column identifier.", call. = FALSE)
  }
  if (any(!nzchar(clusters))) {
    stop("groups.tsv contains an empty cluster value.", call. = FALSE)
  }
  if (anyDuplicated(column_ids)) {
    duplicated <- sort(unique(column_ids[duplicated(column_ids)]))
    stop(sprintf("groups.tsv contains duplicated expression-column ids: %s", paste(duplicated, collapse = ", ")),
         call. = FALSE)
  }

  group_map <- stats::setNames(clusters, column_ids)
  missing <- setdiff(expression_columns, names(group_map))
  if (length(missing) > 0L) {
    stop(sprintf("groups.tsv is missing expression columns: %s", paste(head(missing, 8L), collapse = ", ")),
         call. = FALSE)
  }

  group_map[expression_columns]
}

write_matrix_tsv <- function(matrix_value, path) {
  if (is.null(rownames(matrix_value)) || is.null(colnames(matrix_value))) {
    stop(sprintf("Cannot write matrix without dimnames: %s", path), call. = FALSE)
  }
  out <- data.frame(id = rownames(matrix_value), matrix_value, check.names = FALSE)
  write.table(
    out,
    file = path,
    sep = "\t",
    row.names = FALSE,
    quote = FALSE,
    na = "NA"
  )
}

run_ppcor <- function(observations_by_genes, params, log_path) {
  result <- withCallingHandlers(
    ppcor::pcor(observations_by_genes, method = params$method),
    warning = function(warning_condition) {
      append_log(log_path, sprintf("upstream warning: %s", conditionMessage(warning_condition)))
      invokeRestart("muffleWarning")
    }
  )
  if (!is.list(result) || is.null(result$estimate) || !is.matrix(result$estimate)) {
    stop("ppcor::pcor() did not return a result list with an estimate matrix.", call. = FALSE)
  }
  gene_ids <- colnames(observations_by_genes)
  for (matrix_name in c("estimate", "p.value", "statistic")) {
    if (!is.null(result[[matrix_name]]) && is.matrix(result[[matrix_name]])) {
      dimnames(result[[matrix_name]]) <- list(gene_ids, gene_ids)
    }
  }
  result
}

build_network <- function(estimate_matrix) {
  genes <- rownames(estimate_matrix)
  if (is.null(genes) || is.null(colnames(estimate_matrix)) || !identical(genes, colnames(estimate_matrix))) {
    stop("ppcor estimate matrix must have identical row and column gene ids.", call. = FALSE)
  }
  if (length(genes) < 2L) {
    stop("ppcor requires at least two genes to export a network.", call. = FALSE)
  }

  rows <- list()
  for (i in seq_len(length(genes) - 1L)) {
    for (j in seq.int(i + 1L, length(genes))) {
      coefficient <- as.numeric(estimate_matrix[i, j])
      if (!is.finite(coefficient) || coefficient == 0) {
        next
      }
      rows[[length(rows) + 1L]] <- data.frame(
        source = genes[[i]],
        target = genes[[j]],
        score = abs(coefficient),
        sign = ifelse(coefficient > 0, "+", "-"),
        evidence = "association",
        context = "global",
        stringsAsFactors = FALSE
      )
    }
  }

  if (!length(rows)) {
    stop("ppcor produced no finite non-zero partial-correlation edges.", call. = FALSE)
  }

  out <- do.call(rbind, rows)
  out <- out[order(-out$score, out$source, out$target), , drop = FALSE]
  rownames(out) <- NULL
  out[, NETWORK_COLUMNS, drop = FALSE]
}

write_config <- function(path, params, expression_data, execution_mode, group_map, threads) {
  config <- list(
    tool = "ppcor",
    upstream_package = "ppcor",
    upstream_version = as.character(utils::packageVersion("ppcor")),
    entrypoint = "ppcor::pcor",
    execution_mode = execution_mode,
    gene_count = nrow(expression_data),
    expression_column_count = ncol(expression_data),
    requested_threads = threads,
    upstream_threads = 1L,
    params = params,
    group_count = if (is.null(group_map)) NULL else length(unique(unname(group_map))),
    score_rule = "score=abs(ppcor::pcor(...)$estimate); sign stores coefficient direction"
  )
  writeLines(toJSON(config, auto_unbox = TRUE, null = "null", pretty = TRUE), path, useBytes = TRUE)
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
  if (threads != 1L) {
    stop("ppcor exposes no upstream thread control; --threads must be 1.", call. = FALSE)
  }

  if (!file.exists(input_path)) stop(sprintf("Input file not found: %s", input_path), call. = FALSE)
  if (!file.exists(params_path)) stop(sprintf("Params file not found: %s", params_path), call. = FALSE)
  if (!dir.exists(extra_dir)) stop(sprintf("Extra directory not found: %s", extra_dir), call. = FALSE)

  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
  raw_dir <- file.path(output_dir, "raw")
  dir.create(raw_dir, recursive = TRUE, showWarnings = FALSE)

  progress_path <- file.path(output_dir, "progress.json")
  log_path <- file.path(output_dir, "ppcor.log")
  network_path <- file.path(output_dir, "network.csv")

  write_progress(progress_path, "running", 0L, "init", "Initializing ppcor wrapper")
  append_log(log_path, "ppcor wrapper starting")

  tryCatch({
    raw_params <- load_json_object(params_path, "params.json")
    params <- resolve_params(raw_params)
    execution_mode <- load_execution_mode(params_path)

    write_progress(progress_path, "running", 10L, "load_input", "Loading expression matrix")
    expression_data <- read_expression_tsv(input_path)
    group_map <- NULL
    if (execution_mode == "group_emulated") {
      group_map <- read_groups(extra_dir, colnames(expression_data))
    }

    observations_by_genes <- t(expression_data)
    append_log(log_path, sprintf(
      "running ppcor::pcor method=%s genes=%d columns=%d execution_mode=%s",
      params$method,
      nrow(expression_data),
      ncol(expression_data),
      execution_mode
    ))

    write_progress(progress_path, "running", 35L, "inference", "Running ppcor::pcor")
    result <- run_ppcor(observations_by_genes, params, log_path)

    write_progress(progress_path, "running", 75L, "write_raw", "Writing raw ppcor matrices")
    write_matrix_tsv(result$estimate, file.path(raw_dir, "estimate.tsv"))
    write_matrix_tsv(result$p.value, file.path(raw_dir, "p.value.tsv"))
    write_matrix_tsv(result$statistic, file.path(raw_dir, "statistic.tsv"))
    write_config(
      file.path(raw_dir, "ppcor_config.json"),
      params,
      expression_data,
      execution_mode,
      group_map,
      threads
    )

    write_progress(progress_path, "running", 90L, "write_output", "Writing network.csv")
    network_df <- build_network(result$estimate)
    write.csv(network_df, network_path, row.names = FALSE)

    append_log(log_path, sprintf("ppcor completed with %d exported edges", nrow(network_df)))
    write_progress(
      progress_path,
      "completed",
      100L,
      "done",
      "ppcor completed successfully"
    )
  }, error = function(exc) {
    append_log(log_path, sprintf("wrapper failure: %s", conditionMessage(exc)))
    write_progress(
      progress_path,
      "failed",
      100L,
      "error",
      "ppcor failed",
      error = conditionMessage(exc)
    )
    stop(exc)
  })
}

main()
