import cern.jet.random.engine.MersenneTwister;
import islab.bayesian.genenetwork.GeneNetwork;
import islab.bayesian.genenetwork.Node;
import islab.bayesian.genenetwork.generation.IniSettings;
import islab.bayesian.genenetwork.generation.NetworkGeneratorCLI;
import islab.lib.RandomElement;
import islab.lib.XmlHelper;
import org.apache.commons.math.random.RandomDataImpl;

/** Seed the auxiliary expression-scale stream omitted by SynTReN 1.2.
 *
 * The upstream JAR remains unchanged. Its network generation and expression
 * simulation APIs are used verbatim. Between those stages, replace only the
 * unseeded maximum-expression draws with draws from the same Commons Math
 * exponential distribution (mean 5000), seeded from the requested run seed.
 * This independent stream does not consume the topology/kinetics RNG state.
 */
public final class SyntrenSeededCLI {
    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("Usage: SyntrenSeededCLI <ini-file>");
        }
        IniSettings settings = new IniSettings(args[0]);
        boolean generateExpression = settings.generateExpressionData;
        settings.generateExpressionData = false;
        NetworkGeneratorCLI.run(settings);

        // Do not alter an externally supplied, already parameterized XML model.
        if (settings.createGeneNetwork) {
            GeneNetwork network = GeneNetwork.fromXMLFile(
                new RandomElement(new MersenneTwister(settings.randomSeed)),
                settings.xmlFile);
            if (network == null) {
                throw new IllegalStateException("SynTReN did not create its network model");
            }
            RandomDataImpl expressionScale = new RandomDataImpl();
            expressionScale.reSeed((long) settings.randomSeed);
            for (Object value : network.getNodes()) {
                ((Node) value).setMaxExpression(expressionScale.nextExponential(5000.0));
            }
            XmlHelper.saveToFile(settings.xmlFile, network.toXML(""));
        }

        if (generateExpression) {
            settings.generateExpressionData = true;
            NetworkGeneratorCLI.generateData(settings);
        }
    }
}
