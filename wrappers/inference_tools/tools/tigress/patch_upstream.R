# Narrow repairs to the pinned public implementation; fail if source changes.
root <- commandArgs(trailingOnly = TRUE)[[1L]]
replace_exact <- function(path, old, new) {
  text <- paste(readLines(path, warn = FALSE), collapse = "\n")
  if (!grepl(old, text, fixed = TRUE)) stop(paste("Missing TIGRESS patch anchor:", old))
  text <- gsub(old, new, text, fixed = TRUE)
  writeLines(text, path, useBytes = TRUE)
}
replace_exact(file.path(root, "R", "tigress.R"),
              "nsteps=nstepsLARS, alpha=alpha)",
              "nsteps=nstepsLARS, alpha=alpha, scoring=scoring)")
replace_exact(file.path(root, "R", "tigress.R"),
              '"scorestokeep", "allsteps"',
              '"scorestokeep", "allsteps", "scoring"')
for (half in c("i1", "i2")) {
  replace_exact(file.path(root, "R", "stabilityselection.R"),
                paste0("if (sd(y[", half, "]>0))"),
                paste0("if (sd(y[", half, "])>0)"))
}
