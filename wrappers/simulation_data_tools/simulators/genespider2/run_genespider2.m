function run_genespider2(request_path, output_dir)
% Execute the public GeneSPIDER2 MATLAB API for the ANDREA wrapper.
%
% Python owns ANDREA validation and normalization. This MATLAB entrypoint
% intentionally only calls GeneSPIDER2 functions and writes raw matrices.

if nargin < 2
    error('run_genespider2 requires request_path and output_dir.');
end

raw = fileread(request_path);
request = jsondecode(raw);

if ~exist(output_dir, 'dir')
    mkdir(output_dir);
end

genespider_home = getenv('GENESPIDER2_HOME');
if isempty(genespider_home)
    genespider_home = '/opt/genespider';
end
if ~isdeployed
    addpath(genpath(genespider_home));
end

rng(double(request.seed), 'twister');
if isfield(request, 'threads')
    try
        maxNumCompThreads(double(request.threads));
    catch ME
        warning('ANDREA:GeneSPIDER2:Threads', 'maxNumCompThreads failed: %s', ME.message);
    end
end

A = resolve_network(request);
writematrix(A, fullfile(output_dir, 'network_matrix.tsv'), 'FileType', 'text', 'Delimiter', '\t');

mode = char(request.mode);
switch mode
    case 'bulk_perturbational'
        P = readmatrix(char(request.perturbation_matrix_path), 'FileType', 'text', 'Delimiter', '\t');
        Net = datastruct.Network(A, 'andrea_bulk_perturbational');
        X = Net.G * P;
        [E, stdE] = datastruct.noise( ...
            X, P, ...
            'SNR', double(request.bulk_snr), ...
            'SNR_model', string(request.bulk_snr_model));
        Y = X + E;
        writematrix(P, fullfile(output_dir, 'perturbation_design_matrix.tsv'), 'FileType', 'text', 'Delimiter', '\t');
        writematrix(X, fullfile(output_dir, 'bulk_noise_free_response.tsv'), 'FileType', 'text', 'Delimiter', '\t');
        writematrix(E, fullfile(output_dir, 'bulk_noise_matrix.tsv'), 'FileType', 'text', 'Delimiter', '\t');
        writematrix(stdE, fullfile(output_dir, 'bulk_noise_std.tsv'), 'FileType', 'text', 'Delimiter', '\t');
        writematrix(Y, fullfile(output_dir, 'expression_matrix.tsv'), 'FileType', 'text', 'Delimiter', '\t');
        save(fullfile(output_dir, 'bulk_perturbational_raw.mat'), 'A', 'P', 'X', 'E', 'stdE', 'Y');

    case 'bulk_time_series'
        p = readmatrix(char(request.time_series_perturbation_vector_path), 'FileType', 'text', 'Delimiter', '\t');
        L = datastruct.simts( ...
            A, p, ...
            double(request.time_points), ...
            double(request.input_noise_std), ...
            false);
        writematrix(p, fullfile(output_dir, 'time_series_perturbation_vector.tsv'), 'FileType', 'text', 'Delimiter', '\t');
        writematrix(L, fullfile(output_dir, 'expression_matrix.tsv'), 'FileType', 'text', 'Delimiter', '\t');
        save(fullfile(output_dir, 'bulk_time_series_raw.mat'), 'A', 'p', 'L');

    case 'single_cell_perturbational'
        P = readmatrix(char(request.perturbation_matrix_path), 'FileType', 'text', 'Delimiter', '\t');
        [Y, X, Ed, Eg, SCC] = datastruct.scdata( ...
            A, P, ...
            'SNR', double(request.single_cell_snr), ...
            'SNRc', double(request.single_cell_control_snr), ...
            'SNR_model', string(request.single_cell_snr_model), ...
            'raw_counts', logical(request.single_cell_raw_counts), ...
            'right_tail', double(request.single_cell_right_tail), ...
            'negbin_prob', double(request.single_cell_negbin_prob), ...
            'disper', double(request.single_cell_dispersion), ...
            'n_clusts', double(request.single_cell_n_clusts), ...
            'logbase', double(request.single_cell_logbase), ...
            'ds_min', double(request.single_cell_ds_min), ...
            'ds_max', double(request.single_cell_ds_max));
        writematrix(P, fullfile(output_dir, 'perturbation_design_matrix.tsv'), 'FileType', 'text', 'Delimiter', '\t');
        writematrix(Y, fullfile(output_dir, 'expression_matrix.tsv'), 'FileType', 'text', 'Delimiter', '\t');
        writematrix(X, fullfile(output_dir, 'single_cell_noise_free_fold_change.tsv'), 'FileType', 'text', 'Delimiter', '\t');
        writematrix(Ed, fullfile(output_dir, 'single_cell_dropout_mask.tsv'), 'FileType', 'text', 'Delimiter', '\t');
        writematrix(Eg, fullfile(output_dir, 'single_cell_gaussian_noise.tsv'), 'FileType', 'text', 'Delimiter', '\t');
        writematrix(SCC, fullfile(output_dir, 'single_cell_control_counts.tsv'), 'FileType', 'text', 'Delimiter', '\t');
        save(fullfile(output_dir, 'single_cell_perturbational_raw.mat'), 'A', 'P', 'Y', 'X', 'Ed', 'Eg', 'SCC');

    otherwise
        error('Unsupported GeneSPIDER2 mode: %s', mode);
end

session_info = fopen(fullfile(output_dir, 'matlab_session_info.txt'), 'w');
fprintf(session_info, 'matlab_version=%s\n', version);
fprintf(session_info, 'genespider_home=%s\n', genespider_home);
fprintf(session_info, 'mode=%s\n', mode);
fclose(session_info);
end

function A = resolve_network(request)
network_source = char(request.network_source);
switch network_source
    case 'input_tsv'
        A = readmatrix(char(request.input_network_matrix_path), 'FileType', 'text', 'Delimiter', '\t');
    case 'scalefree2'
        A = datastruct.scalefree2( ...
            double(request.num_genes), ...
            double(request.average_degree), ...
            'alpha', double(request.scalefree2_alpha), ...
            'pasign', double(request.activation_probability));
    otherwise
        error('Unsupported network_source: %s', network_source);
end
end
