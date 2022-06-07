package org.lflang.federated;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import org.eclipse.emf.ecore.resource.Resource;
import org.eclipse.xtext.nodemodel.util.NodeModelUtils;

import org.lflang.ASTUtils;
import org.lflang.ErrorReporter;
import org.lflang.generator.LFGeneratorContext;
import org.lflang.lf.Instantiation;
import org.lflang.lf.Reactor;

public class FedGenerator {

    private FedFileConfig fileConfig;

    public FedGenerator(FedFileConfig fileConfig, ErrorReporter errorReporter) {

        this.fileConfig = fileConfig;
    }
    public boolean doGenerate(Resource resource, LFGeneratorContext context) throws IOException {
        Reactor fedReactor = FedASTUtils.findFederatedReactor(resource);
        for (Instantiation fedInstantiation : fedReactor.getInstantiations()) {
            System.out.println("Generating code for federate " + fedInstantiation.getName() + " in directory "
                                   + fileConfig.getFedSrcPath());
            Files.createDirectories(fileConfig.getFedSrcPath());

            Path lfFilePath = fileConfig.getFedSrcPath().resolve(fedInstantiation.getName() + ".lf");
            try (var srcWriter = Files.newBufferedWriter(lfFilePath)) {
                srcWriter.write(NodeModelUtils.getNode(fedReactor.eContainer()).getText());
            }

        }
        return false;
    }

    // private void generateFederate();
}
