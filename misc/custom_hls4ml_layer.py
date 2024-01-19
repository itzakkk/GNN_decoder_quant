from pathlib import Path

import hls4ml
import torch
import torch.nn as nn

#workaround until container is fixed
import os
os.environ["LIBRARY_PATH"]="/usr/lib/x86_64-linux-gnu"
os.environ["PATH"]="/tools/Xilinx/Vivado/2020.1/bin/:" + os.environ["PATH"]

@torch.fx.wrap
def pmat_mul(x1, x2):
    return torch.mm(x1, x2)

@torch.fx.wrap
def p_div(a, b):
    return a / b

def pmean_pool(x,  n_nodes, out_dim):

    mean_vec = torch.zeros((1, out_dim))
    n_nodes = n_nodes[0]
    for row in x[:n_nodes, :]:
        for j, el in enumerate(row):
            mean_vec[0, j] += el
    return p_div(mean_vec, n_nodes)
    
    
    # while i < x.shape[0] and (x[i, -1] != 0 or x[i, -2] != 0):
    #     for j, el in enumerate(x[i, :]):
    #         mean_vec[0, j] += el
    #     i += 1
    
    # return p_div(mean_vec, i)

        
        

class HMatMul(hls4ml.model.layers.Layer):
    "hls4ml implementation of a layer doing matrix multiplication"

    def initialize(self):
        assert len(self.inputs) == 2
        inp1 = self.get_input_variable(self.inputs[0])
        inp2 = self.get_input_variable(self.inputs[1])

        out_shape = [inp1.shape[0], inp2.shape[1]]
        dims = [f"OUT_MATMUL_{i}_{self.index}" for i in range(2)]
        self.add_output_variable(out_shape, dims)
        
class HMeanPool(hls4ml.model.layers.Layer):
    "hls4ml implementation of a global mean pooling layer"
    
    def initialize(self):
        assert len(self.inputs) == 1
        inp = self.get_input_variable(self.inputs[0])
        
        out_shape = [1, inp.shape[-1]]
        dims = [f"OUT_MEAN_POOL_{i}_{self.index}" for i in range(2)]
        self.add_output_variable(out_shape, dims)


def parse_matmul_layer(
    operation,
    layer_name,
    input_names,
    input_shapes,
    node,
    x,
    reader,
    config,
):
    layer = {}
    layer["class_name"] = "HMatMul"
    layer["name"] = layer_name
    layer["x_height"] = input_shapes[0][1]
    layer["x_width"] = input_shapes[0][2]
    layer["y_height"] = input_shapes[1][1]
    layer["y_width"] = input_shapes[1][2]

    if input_names is not None:
        layer["inputs"] = input_names

    return layer, [input_shapes[0][1], input_shapes[1][2]]

def parse_mean_pool_layer(
    operation,
    layer_name,
    input_names,
    input_shapes,
    node,
    x,
    reader,
    config,
):
    layer = {}
    layer["class_name"] = "HMeanPool"
    layer["name"] = layer_name
    layer["in_height"] = input_shapes[0][1]
    layer["in_width"] = input_shapes[0][2]

    if input_names is not None:
        layer["inputs"] = input_names

    return layer, [1, input_shapes[0][2]]



matmul_config_template = """struct config{index} : nnet::matmul_config {{
    static const unsigned x_height = {x_height};
    static const unsigned x_width = {x_width};
    static const unsigned y_height = {y_height};
    static const unsigned y_width = {y_width};
}};\n"""

mean_pool_config_template = """struct config{index} : nnet::mean_pool_config {{
    static const unsigned in_height = {in_height};
    static const unsigned in_width = {in_width}; 
}};\n"""

matmul_function_template = "nnet::matmul<{input_t}, {config}>({x}, {y}, {res});"
matmul_include_list = ["nnet_utils/nnet_matmul.h"]

mean_pool_function_template = "nnet::mean_pool<{input_t}, {config}>({in}, {out});"
mean_pool_include_list = ["nnet_utils/nnet_mean_pool.h"]


class HMatMulConfigTemplate(hls4ml.backends.template.LayerConfigTemplate):
    def __init__(self):
        super().__init__(HMatMul)
        self.template = matmul_config_template

    def format(self, node):
        params = self._default_config_params(node)
        params["x_height"] = node.get_input_variable(node.inputs[0]).shape[0]
        params["x_width"] = node.get_input_variable(node.inputs[0]).shape[1]
        params["y_height"] = node.get_input_variable(node.inputs[1]).shape[0]
        params["y_width"] = node.get_input_variable(node.inputs[1]).shape[1]

        return self.template.format(**params)
    
class HMeanPoolConfigTemplate(hls4ml.backends.template.LayerConfigTemplate):
    def __init__(self):
        super().__init__(HMeanPool)
        self.template = mean_pool_config_template
        
    def format(self, node):
        params = self._default_config_params(node)
        params["in_height"] = node.get_input_variable(node.inputs[0]).shape[0]
        params["in_width"] = node.get_input_variable(node.inputs[0]).shape[1]
        
        return self.template.format(**params)

class HMatMulFunctionTemplate(hls4ml.backends.template.FunctionCallTemplate):
    def __init__(self):
        super().__init__(HMatMul, include_header=matmul_include_list)
        self.template = matmul_function_template

    def format(self, node):
        params = {}
        params["config"] = f"config{node.index}"
        params["input_t"] = node.get_input_variable(node.inputs[0]).type.name
        params["x"] = node.get_input_variable(node.inputs[0]).name
        params["y"] = node.get_input_variable(node.inputs[1]).name
        params["res"] = node.get_output_variable().name

        return self.template.format(**params)

class HMeanPoolFunctionTemplate(hls4ml.backends.template.FunctionCallTemplate):
    def __init__(self):
        super().__init__(HMeanPool, include_header=mean_pool_include_list)
        self.template = mean_pool_function_template
        
    def format(self, node):
        params = {}
        params["config"] = f"config{node.index}"
        params["input_t"] = node.get_input_variable(node.inputs[0]).type.name
        params["in_data"] = node.get_input_variable(node.inputs[0]).name
        params["out_data"] = node.get_output_variable().name

        return self.template.format(**params)


class CustomGraphConv(nn.Module):
    def __init__(self, input_features, output_features):
        super().__init__()

        self.weight_node = nn.Linear(input_features, output_features, bias=False)
        self.weight_adj = nn.Linear(input_features, output_features, bias=True)

    def forward(self, x, adj):
        node_term = self.weight_node(x)

        adjaceny_sum = pmat_mul(adj, x)
        adjaceny_term = self.weight_adj(adjaceny_sum)

        return node_term + adjaceny_term
    
class SimpleGraphNet(torch.nn.Module):
    def __init__(
        self,
        hidden_channels_GCN=[4, 8],
        hidden_channels_MLP=[8, 4],
        num_node_features=2,
        num_classes=1,
    ):
        super().__init__()
        self.activation = nn.ReLU()
        
        channels = [num_node_features] + hidden_channels_GCN
        self.graph_layers = nn.ModuleList(
            [
                CustomGraphConv(in_channels, out_channels)
                for (in_channels, out_channels) in zip(channels[:-1], channels[1:])
            ]
        )
        
        # Dense layers
        channels = hidden_channels_GCN[-1:] + hidden_channels_MLP
        self.dense_layers = nn.ModuleList(
            [
                nn.Linear(in_channels, out_channels)
                for (in_channels, out_channels) in zip(channels[:-1], channels[1:])
            ]
        )

        # Output later
        self.output_layer = nn.Linear(hidden_channels_MLP[-1], num_classes)
        
    def forward(self, x, adj, batch):
        
        #  node embeddings
        for layer in self.graph_layers:
            x = layer(x, adj)
            x = self.activation(x)
            
        # pool
        x = pmat_mul(batch, x)
        
        # dense
        for layer in self.dense_layers:
            x = layer(x)
            x = self.activation(x)

        # output
        x = self.output_layer(x)


class GraphWTorchNet(torch.nn.Module):
    def __init__(
        self,
        hidden_channels_GCN=[32, 128, 256, 512, 512, 256, 256],
        hidden_channels_MLP=[256, 128, 64],
        num_node_features=5,
        num_classes=1,
        manual_seed=12345,
    ):
        # num_classes is 1 for each head
        super().__init__()
        if manual_seed is not None:
            torch.manual_seed(manual_seed)

        # Activation
        self.activation = nn.ReLU()

        # Average pooling
        self.pool_dim = hidden_channels_GCN[-1]

        # GCN layers
        channels = [num_node_features] + hidden_channels_GCN
        self.graph_layers = nn.ModuleList(
            [
                CustomGraphConv(in_channels, out_channels)
                for (in_channels, out_channels) in zip(channels[:-1], channels[1:])
            ]
        )

        # Dense layers
        channels = hidden_channels_GCN[-1:] + hidden_channels_MLP
        self.dense_layers = nn.ModuleList(
            [
                nn.Linear(in_channels, out_channels)
                for (in_channels, out_channels) in zip(channels[:-1], channels[1:])
            ]
        )

        # Output later
        self.output_layer = nn.Linear(hidden_channels_MLP[-1], num_classes)

    def forward(self, x, adj, batch):
        #  node embeddings
        for layer in self.graph_layers:
            x = layer(x, adj)
            x = self.activation(x)

        # global mean pool
        x = pmat_mul(batch, x)
        # x = pmean_pool(x, n_nodes, self.pool_dim)
        # x = torch.nn.functional.avg_pool1d(x.T, kernel_size=80).T

        for layer in self.dense_layers:
            x = layer(x)
            x = self.activation(x)

        # output
        x = self.output_layer(x)

        return x


class SimpleNet(nn.Module):
    def __init__(self):
        super().__init__()

        self.lin1 = nn.Linear(64, 32)
        self.lin2 = nn.Linear(32, 16)
        self.lin3 = nn.Linear(16, 1)
        
        self.activation = nn.ReLU()
    def forward(self, x):
        x = self.lin1(x)
        x = self.activation(x)
        
        x = self.lin2(x)
        x = self.activation(x)
        
        x = self.lin3(x)
        out = self.activation(x)
        
        return out


def main():
    hls4ml.converters.register_pytorch_layer_handler("pmat_mul", parse_matmul_layer)
    hls4ml.model.layers.register_layer("HMatMul", HMatMul)
    hls4ml.converters.register_pytorch_layer_handler("pmean_pool", parse_mean_pool_layer)
    hls4ml.model.layers.register_layer("HMeanPool", HMeanPool)
    
    backend = hls4ml.backends.get_backend("Vivado")

    backend.register_template(HMatMulConfigTemplate)
    backend.register_template(HMatMulFunctionTemplate)
    backend.register_template(HMeanPoolConfigTemplate)
    backend.register_template(HMeanPoolFunctionTemplate)

    p = Path(__file__).parent / "nnet_matmul.h"
    print(f"Registering custom template at {p}.")
    backend.register_source(p)
    
    p = Path(__file__).parent / "nnet_mean_pool.h"
    print(f"Registering custom template at {p}.")
    backend.register_source(p)
    
    simple = False
    
    if simple:
        # SIMPLE EXAMPLE WORKING
        #---------------------------------------------------------------------
        model = SimpleNet()
        hls_config = {}
        hls_config["Model"] = {
            "Precision": "ap_fixed<16,2>",
            "ReuseFactor": 1,
            "Strategy": "Resource",
        }
        input_shape = [[None, 1, 64]]
        hmodel = hls4ml.converters.convert_from_pytorch_model(
            model,
            input_shape,
            output_dir="test",
            project_name="simple_nn",
            backend="Vivado",
            hls_config=hls_config,
            io_type="io_stream",
        )
        hmodel.build()
        #----------------------------------------------------------------------
    else:
        # MORE REALISTIC EXAMPLE
        model = SimpleGraphNet()
        hls_config = {}
        hls_config["Model"] = {
            "Precision": "ap_fixed<6,2>",
            "ReuseFactor": 1,
            "Strategy": "Resource",
        }

        input_shape = [[None, 10, 2], [None, 10, 10], [None, 1, 10]]
        hmodel = hls4ml.converters.convert_from_pytorch_model(
            model,
            input_shape,
            output_dir="graph_nn_as_hls",
            project_name="quant_on_fpga",
            backend="Vivado",
            hls_config=hls_config,
            io_type="io_parallel",
        )
        hmodel.build()
    
    
if __name__ == "__main__":
    main()