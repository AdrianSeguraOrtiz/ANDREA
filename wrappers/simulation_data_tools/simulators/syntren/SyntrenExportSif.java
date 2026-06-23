import java.io.FileWriter;
import java.io.PrintWriter;

import cern.jet.random.engine.MersenneTwister;
import islab.bayesian.genenetwork.GeneNetwork;
import islab.lib.RandomElement;

public class SyntrenExportSif {
    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: SyntrenExportSif input.xml output.sif");
        }
        GeneNetwork network = GeneNetwork.fromXMLFile(new RandomElement(new MersenneTwister(1)), args[0]);
        try (PrintWriter writer = new PrintWriter(new FileWriter(args[1]))) {
            network.toSIF(writer);
        }
    }
}
