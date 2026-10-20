# Read-only calibration validation, outside the measured container duration.
# Reconstruct dyngen 1.1.1's allocation without drawing new random samples.
suppressPackageStartupMessages(library(dyngen))
suppressPackageStartupMessages(library(dplyr))
stopifnot(as.character(packageVersion("dyngen")) == "1.1.1")
root <- commandArgs(trailingOnly = TRUE)[[1]]
raw <- file.path(root, "provenance", "raw")
model <- readRDS(file.path(raw, "model.rds"))
dataset <- readRDS(file.path(raw, "dataset.rds"))
request <- jsonlite::read_json(file.path(raw, "simulator-run-request.json"), simplifyVector = TRUE)
requested <- as.integer(request$params$num_cells)
stopifnot(length(requested) == 1, requested > 0, model$numbers$num_cells == requested)
kind <- request$params$experiment_params$kind
expected <- requested
missing_segments <- singleton_segments <- dropped <- 0L
if (identical(kind, "snapshot")) {
  stopifnot(identical(model$experiment_params$fun, dyngen:::.generate_experiment_snapshot))
  steps <- model$simulations$meta %>% group_by(from, to) %>%
    summarise(num_steps = n(), .groups = "drop")
  network <- model$gold_standard$network
  missing_segments <- nrow(anti_join(network, steps, by = c("from", "to")))
  allocation <- inner_join(network, steps, by = c("from", "to")) %>%
    mutate(num_cells = diff(c(0, round(cumsum(length / sum(length)) * requested))))
  stopifnot(nrow(allocation) > 0, sum(allocation$num_cells) == requested)
  # Upstream returns NULL for a branch with only one available step. It does
  # not redistribute that branch's allocation, so fewer cells can be returned.
  singleton_segments <- sum(allocation$num_steps == 1)
  dropped <- sum(allocation$num_cells[allocation$num_steps == 1])
  expected <- requested - dropped
} else {
  stopifnot(identical(kind, "synchronised"),
            identical(model$experiment_params$fun, dyngen:::.generate_experiment_synchronised))
}
counts <- as.matrix(dataset$counts)
export <- read.delim(file.path(root, "expression.tsv"), check.names = FALSE,
                     row.names = 1, stringsAsFactors = FALSE)
export <- t(as.matrix(export))
stopifnot(expected > 0, nrow(counts) == expected,
          nrow(model$experiment$cell_info) == expected,
          identical(rownames(counts), as.character(dataset$cell_ids)),
          identical(rownames(counts), as.character(model$experiment$cell_info$cell_id)),
          identical(dimnames(counts), dimnames(export)),
          identical(dim(counts), dim(export)), all(counts == export))
cat(jsonlite::toJSON(list(
  kind = kind, requested_cells = requested, expected_cells = expected,
  observed_cells = nrow(counts), missing_segments = missing_segments,
  singleton_segments = singleton_segments, dropped_cells = dropped,
  expression_matches_native = TRUE, dyngen_version = "1.1.1"
), auto_unbox = TRUE))
