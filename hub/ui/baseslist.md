<title>Bases list</title>



# bases:

<div id="bases"></div>
<script>//fetch bases list and display on page
    fetch('/v1/managers/bases')
        .then(res => res.json())
        .then(data => {
            document.getElementById('bases').textContent = data.bases;
            console.log(data)
        })
        .catch(err => {
            document.getElementById('bases').textContent = 'Error loading bases';
        });
</script>







<script src='corner_menue.js'>//script for corner menu</script>