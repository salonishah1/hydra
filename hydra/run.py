#!/usr/bin/env python2.7
import argparse
import bnpy
import os
import logging
import multiprocessing
import numpy as np
import pandas as pd

from collections import defaultdict

from library.utils import mkdir_p, parallel_fit
from library.notebook import create_notebook

src = os.path.dirname(os.path.abspath(__file__))


def get_genesets(dirs):
    """
    Formats the paths to the gene set files

    :param list dirs: Gene set directories
    :return: Path to gene set files
    :rtype: list
    """
    pths = []
    for d in dirs:
        logging.info("Pulling %s gene sets:" % d)
        gs_dir = os.path.join(src, 'gene-sets', d)
        gss = os.listdir(gs_dir)
        for s in gss:
            logging.info("\t%s" % s)
            gs_pth = os.path.join(gs_dir, s)
            pths.append(gs_pth)

    return pths


def get_test_genesets():
    d = os.path.join(src, 'test', 'gene-sets')
    sets = os.listdir(d)
    logging.info("Available Gene Sets:")
    for s in sets:
        logging.info(s)
    sets = [os.path.join(d, s) for s in sets]
    return sets


def read_genesets(sets):
    gs = defaultdict(set)

    for s in sets:
        name = os.path.basename(s)
        with open(s) as f:
            for line in f:
                gene = line.strip()
                gs[name].add(gene)
    return gs


analyzed = {}
def is_multimodal(name, data, min_prob_filter=None):
    if name in analyzed:
        return analyzed[name]

    X = bnpy.data.XData(data)
    model = parallel_fit(name, X)
    probs = model.allocModel.get_active_comp_probs()
    min_prob = np.min(probs)

    # Remove genes that have a low component frequency
    if min_prob < min_prob_filter:
        analyzed[name] = (name, False)
        return name, False

    elif len(probs) > 1:
        analyzed[name] = (name, True)
        return name, True

    else:
        analyzed[name] = (name, False)
        return name, False


def filter_geneset(lst, matrx, CPU=1, gene_mean_filter=None, min_prob_filter=None):
    """

    :param lst: List of genes
    :param matrx: Expression dataframe
    :return:
    """
    pool = multiprocessing.Pool(processes=CPU)

    results = []
    for gene in lst:
        data = matrx.loc[gene, :].values
        mean = np.mean(data)

        # Skip Genes that have a mean below the mean filter
        if mean < gene_mean_filter:
            continue

        data = data - mean
        data = data.reshape(len(data), 1)
        res = pool.apply_async(is_multimodal, args=(gene, data, min_prob_filter,))
        results.append(res)

    output = [x.get() for x in results]

    # Remove duplicate genes
    return list(set([x[0] for x in output if x[1] is True]))


def find_aliases(gss, mapper, index):
    """
    Removes genes that do not overlap with matrix. Will
    try to rescue genes by mapping them to an alias that
    overlaps.

    :param gss:
    :param mapper:
    :param index:
    :return:
    """
    for gs, genes in gss.items():
        filtered = []
        for gene in genes:
            if gene in index:
                filtered.append(gene)

            else:
                aliases = mapper.loc[mapper['gene'] == gene, 'aliases']
                if len(aliases) > 0:
                    for alias in aliases:
                        matches = [x for x in alias.split('|') if x in index]
                        if len(matches) > 0:
                            for match in matches:
                                logging.info("Replacing %s in %s with %s" % (gene,
                                                                             gs,
                                                                             match))
                                filtered.append(match)
        # Remove duplicates
        gss[gs] = list(set(filtered))
    return gss


def main():
    """
    Fits one, two, and three component mixture models and returns fit information in output dir.
    """
    parser = argparse.ArgumentParser(description=main.__doc__)

    parser.add_argument('-e', '--expression',
                        help='Gene symbol by sample matrix.\nDo not center the data.')

    parser.add_argument('--CPU',
                        dest='CPU',
                        type=int,
                        default=1)

    parser.add_argument('--hallmark',
                        help='Uses hallmark gene sets',
                        dest='hallmark',
                        default=False,
                        action='store_true')

    parser.add_argument('--msigdb',
                        help='Uses MSigDB gene sets',
                        dest='msigdb',
                        default=False,
                        action='store_true')

    parser.add_argument('--treehouse',
                        help='Uses Treehouse gene sets',
                        dest='treehouse',
                        default=False,
                        action='store_true')

    parser.add_argument('--immune',
                        help='Uses curated immune gene sets',
                        dest='immune',
                        default=False,
                        action='store_true')

    parser.add_argument('--min-mean-filter',
                        dest='min_mean_filter',
                        help='Removes genes with an average expression below value.',
                        type=float,
                        default=None)

    parser.add_argument('--min-prob-filter',
                        dest='min_prob_filter',
                        help='Removes genes with a minor component less than value.',
                        type=float,
                        default=None)

    parser.add_argument('--min-num-filter',
                        dest='min_num_filter',
                        help='Skips gene sets with fewer than X multimodal genes.',
                        type=int,
                        default=5)

    parser.add_argument('--debug',
                        action='store_true')

    parser.add_argument('--output-dir',
                        dest='output_dir',
                        default='hydra-out')

    args = parser.parse_args()

    mkdir_p(args.output_dir)

    logging.basicConfig(filename=os.path.join(args.output_dir, 'hydra.log'),
                        level=logging.INFO)

    logging.getLogger().addHandler(logging.StreamHandler())

    logging.info("Started Hydra...")

    logging.info("Parameters:")
    for key, value in vars(args).items():
        logging.info('\t%s: %s' % (key, value))

    # Determine which gene sets are included.
    if args.debug:
        sets = get_test_genesets()

    else:
        dirs = ['misc']

        if args.msigdb:
            dirs = ['msigdb'] + dirs

        if args.hallmark:
            dirs = ['hallmark'] + dirs

        if args.treehouse:
            dirs = ['treehouse'] + dirs

        if args.immune:
            dirs = ['immune'] + dirs

        sets = get_genesets(dirs)

        if len(sets) == 0:
            raise ValueError("Need to specify gene sets for analysis.")

    genesets = read_genesets(sets)

    matrx = pd.read_csv(args.expression,
                        sep='\t',
                        index_col=0)

    # Remove duplicates in index
    matrx = matrx[~matrx.index.duplicated(keep='first')]

    # Find overlap in alias space
    pth = os.path.join(src, 'data/alias-mapper.gz')
    alias_mapper = pd.read_csv(pth, sep='\t')
    genesets = find_aliases(genesets, alias_mapper, matrx.index)

    if args.min_mean_filter is not None:
        logging.info("Minimum gene expression mean: %0.2f" % args.min_mean_filter)

    if args.min_prob_filter is not None:
        logging.info("Minimum component probability: %0.2f" % args.min_prob_filter)

    filtered_genesets = defaultdict(set)
    for gs, genes in genesets.items():

        start = len(genes)
        filtered_genesets[gs] = filter_geneset(list(genes),
                                               matrx,
                                               CPU=args.CPU,
                                               gene_mean_filter=args.min_mean_filter,
                                               min_prob_filter=args.min_prob_filter)
        end = len(filtered_genesets[gs])

        logging.info("Filtering: {gs} went from {x} to {y} genes".format(gs=gs,
                                                                         x=start,
                                                                         y=end))
        if end < args.min_num_filter:
            logging.info("Skipping {gs} because there are not enough genes".format(gs=gs))
            continue

        # Make directory for output
        gsdir = os.path.join(args.output_dir, gs)
        mkdir_p(gsdir)

        # Center data to make inference easier
        center = matrx.loc[filtered_genesets[gs], :].apply(lambda x: x - x.mean(), axis=1)

        # Trying to figure out where the duplicate index is coming from
        center = center[~center.index.duplicated(keep='first')]

        # Save the expression data for future analysis
        pth = os.path.join(gsdir, 'expression.tsv')
        matrx.loc[filtered_genesets[gs]].to_csv(pth, sep='\t')

        # Need to take the transpose
        # Samples x Genes
        data = center.T.values

        # Create dataset object for inference
        dataset = bnpy.data.XData(data)

        # Fit multivariate model
        hmodel = parallel_fit(gs, dataset, save_output=args.debug)

        # Get the sample assignments
        LP = hmodel.calc_local_params(dataset)
        asnmts = LP['resp'].argmax(axis=1)

        pth = os.path.join(gsdir, 'assignments.tsv')
        with open(pth, 'w') as f:
            for sample, asnmt in zip(center.columns, asnmts):
                f.write('%s\t%s\n' % (sample, asnmt))

        # Save model
        bnpy.ioutil.ModelWriter.save_model(hmodel,
                                           gsdir,
                                           prefix=gs)

        create_notebook(gs, gsdir)

        if args.debug:
            break

if __name__ == '__main__':
    main()
